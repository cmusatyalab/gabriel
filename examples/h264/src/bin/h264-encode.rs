//! A Gabriel engine that encodes raw RGB frames (SteelEagle `RawFrame`s) with H.264, and emits
//! each as an `EncodedVideoFrame`, e.g. for `h264-decode` on a remote server.
//!
//! Frames reach the decoder only if Gabriel carries them there: the crossing to the remote server
//! may skip a frame for a newer one, or time it out. Each P-frame needs the frames before it, so
//! the encoder subscribes to the decoder's events, and makes its next frame a keyframe once a
//! frame it encoded since its last keyframe does not reach the decoder.

use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use clap::Parser;
use gabriel::{Engine, EventKind, InputId, subscription::Event};
use gabriel_h264::{
    DecodeResult, EncodedVideoFrame, Pipeline, RGB_CHANNELS, RawFrameView, WAIT_TIMEOUT,
    stop_on_signal, stopped, type_name,
};
use gstreamer::{self as gst};
use gstreamer_video::{VideoFormat, VideoInfo};
use protobuf::Message;

/// Software x264, tuned so that each frame comes out before the next goes in. videoconvert gives
/// the encoder the pixel format it takes. On a Tachyon, the hardware encoder:
/// "qtic2venc control-rate=2 target-bitrate=3000000 b-frames=0".
const DEFAULT_ENCODER: &str =
    "x264enc tune=zerolatency speed-preset=ultrafast bitrate=3000 key-int-max=300";
/// Large enough for a 1080p keyframe at a high bitrate.
const DEFAULT_MAX_OUTPUT_SIZE: usize = 2 * 1024 * 1024;

#[derive(Parser)]
#[command(
    version,
    about = "Encodes raw RGB frames with H.264, as a Gabriel engine"
)]
struct Args {
    /// The engine's name.
    #[arg(long, default_value = "h264-encode")]
    name: String,
    /// The decoder engine, whose events say which frames were lost.
    #[arg(long, default_value = "h264-decode")]
    decoder: String,
    /// The H.264 encoder, in gst-launch syntax.
    #[arg(long, default_value = DEFAULT_ENCODER)]
    encoder: String,
    /// The frame rate the encoder's rate control assumes.
    #[arg(long, default_value_t = 30)]
    fps: u32,
    /// The largest encoded frame, in bytes.
    #[arg(long, default_value_t = DEFAULT_MAX_OUTPUT_SIZE)]
    max_output_size: usize,
    /// The longest one frame may take to encode, in ms.
    #[arg(long, default_value_t = 1000)]
    timeout_ms: u64,
    /// Print a line per frame.
    #[arg(long)]
    log_frames: bool,
}

/// Decides when the next frame must be a keyframe: after a frame the decoder will not get, which
/// breaks the chain of references from the last keyframe.
struct KeyframePolicy {
    needed: bool,
    /// The input the last keyframe was encoded from.
    keyframe: Option<InputId>,
}

impl KeyframePolicy {
    /// The first frame is a keyframe.
    fn new() -> Self {
        Self {
            needed: true,
            keyframe: None,
        }
    }

    /// A frame was emitted for the input `id`.
    fn emitted(&mut self, id: InputId, keyframe: bool) {
        if keyframe {
            self.needed = false;
            self.keyframe = Some(id);
        }
    }

    /// An event of the decoder's.
    fn decoder_event(&mut self, event: &Event<'_>) {
        let event = match event {
            // Frames in flight may have been lost among the events missed.
            Event::Lost(_) => {
                self.needed = true;
                return;
            }
            Event::Published(event) => event,
        };
        match event.kind() {
            // A frame the decoder will not get.
            EventKind::Skipped | EventKind::TimedOut => self.require_if_since_keyframe(event.id()),
            EventKind::Result => {
                if DecodeResult::parse_from_bytes(event.result())
                    .is_ok_and(|result| result.keyframe_needed)
                {
                    self.require_if_since_keyframe(event.id());
                }
            }
            // Frames in flight are lost with the connection.
            EventKind::Disconnected => self.needed = true,
            _ => {}
        }
    }

    /// Requires a keyframe if `id` is the last keyframe's input or a later one. Frames from
    /// before the last keyframe no longer matter.
    fn require_if_since_keyframe(&mut self, id: &InputId) {
        let since = match &self.keyframe {
            Some(keyframe) => {
                id.producer() != keyframe.producer()
                    || id.session() != keyframe.session()
                    || id.seq() >= keyframe.seq()
            }
            None => true,
        };
        self.needed |= since;
    }
}

/// Encodes frames of one size; a frame of another size starts a new pipeline.
struct Encoder<'a> {
    args: &'a Args,
    start: Instant,
    pipeline: Option<(Pipeline, VideoInfo)>,
}

impl<'a> Encoder<'a> {
    fn new(args: &'a Args) -> Self {
        Self {
            args,
            start: Instant::now(),
            pipeline: None,
        }
    }

    /// Encodes the RGB `pixels` of a `width` x `height` frame. Returns the encoded frame, or
    /// None if none came in time, and whether outputs of earlier frames were dropped.
    fn encode(
        &mut self,
        pixels: Vec<u8>,
        width: u32,
        height: u32,
        force_keyframe: bool,
    ) -> Result<(Option<gst::Sample>, bool)> {
        let size_changed = self
            .pipeline
            .as_ref()
            .is_none_or(|(_, info)| info.width() != width || info.height() != height);
        if size_changed {
            // The old pipeline stops before the new one starts.
            self.pipeline = None;
            let pipeline = Pipeline::new(
                &format!(
                    "appsrc name=src ! videoconvert ! {} \
                     ! h264parse config-interval=-1 \
                     ! video/x-h264,stream-format=byte-stream,alignment=au ! appsink name=sink",
                    self.args.encoder
                ),
                &format!(
                    "video/x-raw,format=RGB,width={width},height={height},framerate={}/1",
                    self.args.fps
                ),
            )?;
            let info = VideoInfo::builder(VideoFormat::Rgb, width, height).build()?;
            self.pipeline = Some((pipeline, info));
        }
        let (pipeline, info) = self.pipeline.as_ref().expect("made above");
        if force_keyframe {
            pipeline.force_keyframe();
        }
        let pts = gst::ClockTime::from_nseconds(self.start.elapsed().as_nanos() as u64);
        pipeline.push(rgb_buffer(pixels, info), pts)?;
        Ok(pipeline.pull(pts, Duration::from_millis(self.args.timeout_ms)))
    }
}

/// A buffer of packed RGB `pixels`, laid out as GStreamer lays out `info`: each row rounded up to
/// 4 bytes. Packed rows already are when the width is a multiple of 4, and are used as they are.
fn rgb_buffer(pixels: Vec<u8>, info: &VideoInfo) -> gst::Buffer {
    let row_bytes = info.width() as usize * RGB_CHANNELS as usize;
    let stride = info.stride()[0] as usize;
    if stride == row_bytes {
        return gst::Buffer::from_mut_slice(pixels);
    }
    let mut strided = vec![0; info.size()];
    for (row, packed) in strided.chunks_mut(stride).zip(pixels.chunks(row_bytes)) {
        row[..row_bytes].copy_from_slice(packed);
    }
    gst::Buffer::from_mut_slice(strided)
}

/// Why `frame` cannot be encoded, if it cannot.
fn invalid(frame: &RawFrameView) -> Option<&'static str> {
    if frame.channels != RGB_CHANNELS {
        Some("it is not 3-channel RGB")
    } else if frame.h_res == 0
        || frame.v_res == 0
        || !frame.h_res.is_multiple_of(2)
        || !frame.v_res.is_multiple_of(2)
    {
        Some("its width and height must be even and positive")
    } else if u32::try_from(frame.h_res).is_err() || u32::try_from(frame.v_res).is_err() {
        Some("it is too large")
    } else if frame.data.len() as u64 != frame.h_res * frame.v_res * RGB_CHANNELS {
        Some("its data does not match its size")
    } else {
        None
    }
}

fn main() -> Result<()> {
    let args = Args::parse();
    gst::init()?;
    let stop = stop_on_signal()?;
    let encoded_type = type_name::<EncodedVideoFrame>();

    let mut engine = Engine::new(&args.name, None, args.max_output_size)
        .with_context(|| format!("failed to create engine {}", args.name))?;
    engine.subscribe(&args.decoder)?;
    eprintln!("engine {} ready; watching {}", args.name, args.decoder);

    let mut keyframes = KeyframePolicy::new();
    let mut encoder = Encoder::new(&args);
    let mut frame_number: u64 = 0;
    while !stopped(&stop) {
        match engine.wait(WAIT_TIMEOUT) {
            Ok(()) | Err(gabriel::Error::Again) => {}
            Err(e) => return Err(e.into()),
        }
        loop {
            match engine.poll_event() {
                Ok(event) => keyframes.decoder_event(&event),
                Err(gabriel::Error::Again) => break,
                Err(e) => return Err(e.into()),
            }
        }
        let (id, payload_type, mut frame) = match engine.poll_input() {
            Ok(input) => (
                *input.id(),
                input.payload_type().to_owned(),
                RawFrameView::parse_from_bytes(input.data),
            ),
            Err(gabriel::Error::Again) => continue,
            Err(e) => return Err(e.into()),
        };
        let started = Instant::now();
        let problem = match &frame {
            Ok(frame) => invalid(frame),
            Err(_) => Some("it is not a RawFrame"),
        };
        if let Some(problem) = problem {
            eprintln!("discarding input {}: {problem}", id.seq());
            engine.stop(&[])?;
            continue;
        }
        let frame = frame.as_mut().expect("checked above");

        let forced = keyframes.needed;
        let pixels = std::mem::take(&mut frame.data);
        let encoding = Instant::now();
        let (sample, dropped_late) =
            encoder.encode(pixels, frame.h_res as u32, frame.v_res as u32, forced)?;
        let encode_ms = encoding.elapsed().as_secs_f64() * 1e3;
        // Every frame pushed is one the encoder may refer to later.
        let number = frame_number;
        frame_number += 1;
        if dropped_late {
            // An earlier frame came out too late to send, so the decoder misses it.
            keyframes.needed = true;
        }
        let Some(sample) = sample else {
            eprintln!(
                "frame {number} took over {} ms to encode; discarding it",
                args.timeout_ms
            );
            keyframes.needed = true;
            engine.stop(&[])?;
            continue;
        };

        let buffer = sample.buffer().context("an encoded sample has a buffer")?;
        let keyframe = !buffer.flags().contains(gst::BufferFlags::DELTA_UNIT);
        let mut encoded = EncodedVideoFrame::new();
        encoded.data = buffer.map_readable()?.to_vec();
        encoded.frame_number = number;
        encoded.keyframe = keyframe;
        encoded.raw_frame_type = payload_type;
        // The frame without its pixels; fields this engine does not know are kept.
        encoded.raw_frame = frame.write_to_bytes()?;
        let bytes = encoded.write_to_bytes()?;
        if bytes.len() > args.max_output_size {
            eprintln!(
                "frame {number} is {} bytes, over --max-output-size; discarding it",
                bytes.len()
            );
            keyframes.needed = true;
            engine.stop(&[])?;
            continue;
        }
        engine.new_output()?[..bytes.len()].write_copy_of_slice(&bytes);
        engine.emit(&encoded_type, bytes.len(), &[])?;
        keyframes.emitted(id, keyframe);
        if forced && !keyframe {
            eprintln!("the encoder ignored a request for a keyframe");
        }
        if args.log_frames {
            // ms is the whole frame, from taking the input to emitting it; encode_ms is the
            // pipeline's part.
            eprintln!(
                "frame {number} input {} keyframe={keyframe} bytes={} ms={:.2} encode_ms={encode_ms:.2}",
                id.seq(),
                bytes.len(),
                started.elapsed().as_secs_f64() * 1e3
            );
        }
    }
    Ok(())
}
