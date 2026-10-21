//! A Gabriel engine that decodes the `EncodedVideoFrame`s of `h264-encode`, and emits each as the
//! raw RGB frame it was encoded from, of the same payload type, e.g. a SteelEagle `RawFrame` for a
//! detector.
//!
//! A P-frame decodes correctly only after every frame before it, back to a keyframe. After a gap
//! in the frame numbers, the decoder drops frames until the next keyframe, and says in its result
//! that it needs one; the encoder, which subscribes to the decoder's events, then makes one.

use std::mem::MaybeUninit;
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use clap::Parser;
use gabriel::Engine;
use gabriel_h264::{
    DecodeResult, EncodedVideoFrame, Pipeline, RAW_FRAME_DATA_FIELD, RGB_CHANNELS, RawFrameView,
    WAIT_TIMEOUT, stop_on_signal, stopped, type_name,
};
use gstreamer::{self as gst};
use gstreamer_video::VideoInfo;
use protobuf::{CodedOutputStream, Message, rt::WireType};

/// Software libav, on one thread: frame threading holds frames back.
const DEFAULT_DECODER: &str = "avdec_h264 max-threads=1";
/// Room for the fields of a RawFrame other than its pixels.
const RAW_FRAME_OVERHEAD: usize = 64 * 1024;
/// Timestamps only match inputs to outputs, so any step keeps them apart.
const PTS_STEP: gst::ClockTime = gst::ClockTime::MSECOND;

#[derive(Parser)]
#[command(
    version,
    about = "Decodes H.264 frames to raw RGB frames, as a Gabriel engine"
)]
struct Args {
    /// The engine's name.
    #[arg(long, default_value = "h264-decode")]
    name: String,
    /// The H.264 decoder, in gst-launch syntax.
    #[arg(long, default_value = DEFAULT_DECODER)]
    decoder: String,
    /// The widest frame, in pixels.
    #[arg(long, default_value_t = 1920)]
    max_width: usize,
    /// The tallest frame, in pixels.
    #[arg(long, default_value_t = 1080)]
    max_height: usize,
    /// The longest one frame may take to decode, in ms.
    #[arg(long, default_value_t = 1000)]
    timeout_ms: u64,
    /// Print a line per frame.
    #[arg(long)]
    log_frames: bool,
}

impl Args {
    /// The largest frame the decoder emits, in bytes.
    fn max_output_size(&self) -> usize {
        self.max_width * self.max_height * RGB_CHANNELS as usize + RAW_FRAME_OVERHEAD
    }
}

/// What became of an encoded frame, as its result says.
fn result(decoded: bool, keyframe_needed: bool) -> Result<Vec<u8>> {
    let mut result = DecodeResult::new();
    result.decoded = decoded;
    result.keyframe_needed = keyframe_needed;
    Ok(result.write_to_bytes()?)
}

/// Writes a RawFrame to `out`: `fields`, its fields other than its pixels, then the pixels of
/// `sample`, packed. A message's fields may come in any order, so the pixels go last, copied
/// once, straight from the decoder's buffer. Returns its size, or None if it does not fit.
fn write_raw_frame(
    fields: &[u8],
    sample: &gst::Sample,
    out: &mut [MaybeUninit<u8>],
) -> Result<Option<usize>> {
    let caps = sample.caps().context("the decoder's output has no caps")?;
    let info = VideoInfo::from_caps(caps)?;
    let row_bytes = info.width() as usize * RGB_CHANNELS as usize;
    let stride = info.stride()[0] as usize;
    let pixel_bytes = row_bytes * info.height() as usize;

    let mut header = Vec::new();
    let mut stream = CodedOutputStream::vec(&mut header);
    stream.write_tag(RAW_FRAME_DATA_FIELD, WireType::LengthDelimited)?;
    stream.write_raw_varint64(pixel_bytes as u64)?;
    stream.flush()?;
    drop(stream);

    let size = fields.len() + header.len() + pixel_bytes;
    if size > out.len() {
        return Ok(None);
    }
    let (out_fields, rest) = out.split_at_mut(fields.len());
    out_fields.write_copy_of_slice(fields);
    let (out_header, out_pixels) = rest.split_at_mut(header.len());
    out_header.write_copy_of_slice(&header);
    let buffer = sample.buffer().context("a decoded sample has a buffer")?;
    let pixels = buffer.map_readable()?;
    for (out_row, row) in out_pixels[..pixel_bytes]
        .chunks_mut(row_bytes)
        .zip(pixels.chunks(stride))
    {
        out_row.write_copy_of_slice(&row[..row_bytes]);
    }
    Ok(Some(size))
}

fn main() -> Result<()> {
    let args = Args::parse();
    gst::init()?;
    let stop = stop_on_signal()?;
    let encoded_type = type_name::<EncodedVideoFrame>();
    let decoded = result(true, false)?;
    let dropped = result(false, false)?;
    let keyframe_needed = result(false, true)?;

    let mut engine = Engine::new(
        &args.name,
        Some(&type_name::<DecodeResult>()),
        args.max_output_size(),
    )
    .with_context(|| format!("failed to create engine {}", args.name))?;
    let pipeline = Pipeline::new(
        &format!(
            "appsrc name=src ! h264parse ! {} ! videoconvert ! video/x-raw,format=RGB \
             ! appsink name=sink",
            args.decoder
        ),
        "video/x-h264,stream-format=byte-stream,alignment=au",
    )?;
    eprintln!("engine {} ready", args.name);

    // The number the next frame must have to decode, once a keyframe started the chain.
    let mut expected: Option<u64> = None;
    while !stopped(&stop) {
        match engine.wait(WAIT_TIMEOUT) {
            Ok(()) | Err(gabriel::Error::Again) => {}
            Err(e) => return Err(e.into()),
        }
        let (seq, encoded) = match engine.poll_input() {
            Ok(input) => (
                input.id().seq(),
                (input.payload_type() == encoded_type)
                    .then(|| EncodedVideoFrame::parse_from_bytes(input.data).ok())
                    .flatten(),
            ),
            Err(gabriel::Error::Again) => continue,
            Err(e) => return Err(e.into()),
        };
        let started = Instant::now();
        let Some(encoded) = encoded else {
            eprintln!("discarding input {seq}: not an {encoded_type}");
            engine.drop(&dropped)?;
            continue;
        };
        let frame = RawFrameView::parse_from_bytes(&encoded.raw_frame)?;
        let number = encoded.frame_number;
        if !encoded.keyframe && expected != Some(number) {
            // A frame before this one was lost; until a keyframe, nothing decodes.
            expected = None;
            if args.log_frames {
                eprintln!(
                    "frame {number} input {seq} follows a lost frame; waiting for a keyframe"
                );
            }
            engine.drop(&keyframe_needed)?;
            continue;
        }
        // The decoder keeps this frame as a reference whether or not its output comes in time,
        // so the next one decodes either way.
        expected = Some(number + 1);

        let pts = PTS_STEP * number;
        pipeline.push(gst::Buffer::from_mut_slice(encoded.data), pts)?;
        let (sample, _) = pipeline.pull(pts, Duration::from_millis(args.timeout_ms));
        let Some(sample) = sample else {
            eprintln!(
                "frame {number} took over {} ms to decode; discarding it",
                args.timeout_ms
            );
            engine.drop(&dropped)?;
            continue;
        };
        let info = VideoInfo::from_caps(sample.caps().context("decoded sample has caps")?)?;
        if u64::from(info.width()) != frame.h_res || u64::from(info.height()) != frame.v_res {
            eprintln!(
                "frame {number} decoded to {}x{}, not {}x{}; discarding it",
                info.width(),
                info.height(),
                frame.h_res,
                frame.v_res
            );
            engine.drop(&dropped)?;
            continue;
        }

        let Some(size) = write_raw_frame(&encoded.raw_frame, &sample, engine.new_output()?)? else {
            eprintln!("frame {number} is larger than --max-width x --max-height; discarding it");
            engine.drop(&dropped)?;
            continue;
        };
        engine.replace(&encoded.raw_frame_type, size, &decoded)?;
        if args.log_frames {
            eprintln!(
                "frame {number} input {seq} keyframe={} ms={:.2}",
                encoded.keyframe,
                started.elapsed().as_secs_f64() * 1e3
            );
        }
    }
    Ok(())
}
