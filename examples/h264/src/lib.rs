//! What the H.264 engines share: their messages, and a GStreamer pipeline an engine pushes one
//! buffer into and pulls what it became from.
//!
//! `h264-encode` encodes raw RGB frames (SteelEagle `RawFrame`s) and emits each as an
//! `EncodedVideoFrame`; `h264-decode` decodes those and emits the raw frames again, of the same
//! payload type. A crossing to a remote server may skip or time out a frame, and each P-frame
//! needs the frames before it, so after a lost frame the decoder waits for a keyframe, and the
//! encoder, which subscribes to the decoder's events, makes one.

use std::cell::Cell;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use std::time::Duration;

use anyhow::{Context, Result, anyhow};
use gstreamer::{self as gst, glib::subclass::signal::SignalId, prelude::*};
use gstreamer_app::{AppSink, AppSrc};
use protobuf::MessageFull;

pub mod proto {
    include!(concat!(env!("OUT_DIR"), "/proto/mod.rs"));
}

pub use proto::h264::{DecodeResult, EncodedVideoFrame, RawFrameView};

/// The number of the pixel field of a RawFrame.
pub const RAW_FRAME_DATA_FIELD: u32 = 2;
/// Channels per RGB pixel.
pub const RGB_CHANNELS: u64 = 3;
/// How long one wait for an input may take, so that a stop request is noticed.
pub const WAIT_TIMEOUT: Duration = Duration::from_millis(100);
/// Where a pipeline's timestamps start. An encoder based on GstVideoEncoder shifts every
/// timestamp by 1000 hours when a decoding timestamp would otherwise be negative, as it would be
/// near 0; timestamps that start there are never shifted, so outputs keep their inputs' stamps.
const PTS_BASE: gst::ClockTime = gst::ClockTime::from_seconds(1000 * 60 * 60);
/// The action that makes Qualcomm's encoder (qtic2venc) encode its next frame as a keyframe. It
/// handles no GStreamer event that asks for one.
const IFRAME_TRIGGER_SIGNAL: &str = "trigger-iframe";

/// The payload type of `M`: its full protobuf name.
pub fn type_name<M: MessageFull>() -> String {
    M::descriptor().full_name().to_owned()
}

/// A flag that SIGINT or SIGTERM sets.
pub fn stop_on_signal() -> Result<Arc<AtomicBool>> {
    let stop = Arc::new(AtomicBool::new(false));
    for signal in [signal_hook::consts::SIGINT, signal_hook::consts::SIGTERM] {
        signal_hook::flag::register(signal, Arc::clone(&stop))?;
    }
    Ok(stop)
}

/// Whether `stop` was set.
pub fn stopped(stop: &AtomicBool) -> bool {
    stop.load(Ordering::Relaxed)
}

/// A pipeline, in gst-launch syntax, from an appsrc named "src" to an appsink named "sink". Each
/// buffer pushed is matched to its output by its timestamp.
pub struct Pipeline {
    pipeline: gst::Element,
    src: AppSrc,
    sink: AppSink,
    /// The element with an action that makes the next frame a keyframe, if one has it.
    iframe_trigger: Option<gst::Element>,
    /// Whether a buffer was pushed yet.
    started: Cell<bool>,
}

impl Pipeline {
    /// Builds the pipeline `description`, whose appsrc takes buffers of `src_caps`, and starts it.
    pub fn new(description: &str, src_caps: &str) -> Result<Self> {
        let pipeline = gst::parse::launch(description)
            .with_context(|| format!("invalid pipeline {description:?}"))?;
        let bin = pipeline
            .downcast_ref::<gst::Bin>()
            .context("the pipeline is not a bin")?;
        let element = |name: &str| {
            bin.by_name(name)
                .with_context(|| format!("the pipeline has no element named {name}"))
        };
        let src = element("src")?
            .downcast::<AppSrc>()
            .map_err(|_| anyhow!("src is not an appsrc"))?;
        let sink = element("sink")?
            .downcast::<AppSink>()
            .map_err(|_| anyhow!("sink is not an appsink"))?;
        let iframe_trigger = bin
            .iterate_recurse()
            .into_iter()
            .filter_map(Result::ok)
            .find(|element| SignalId::lookup(IFRAME_TRIGGER_SIGNAL, element.type_()).is_some());
        // A live source of timestamped buffers, each pulled at once at the sink.
        src.set_caps(Some(&src_caps.parse::<gst::Caps>()?));
        src.set_format(gst::Format::Time);
        src.set_is_live(true);
        sink.set_sync(false);
        pipeline
            .set_state(gst::State::Playing)
            .map_err(|_| anyhow!("failed to start the pipeline: {}", bus_error(&pipeline)))?;
        Ok(Self {
            pipeline,
            src,
            sink,
            iframe_trigger,
            started: Cell::new(false),
        })
    }

    /// Pushes `buffer`, stamped `pts`, which must differ from the stamps of other buffers.
    pub fn push(&self, mut buffer: gst::Buffer, pts: gst::ClockTime) -> Result<()> {
        buffer
            .get_mut()
            .expect("a new buffer has one owner")
            .set_pts(PTS_BASE + pts);
        self.started.set(true);
        self.src.push_buffer(buffer).map_err(|e| {
            anyhow!(
                "failed to push a buffer ({e:?}): {}",
                bus_error(&self.pipeline)
            )
        })?;
        Ok(())
    }

    /// The output of the buffer pushed with `pts`, if it comes within `timeout`, and whether
    /// outputs of earlier buffers, which came too late, were dropped first.
    pub fn pull(&self, pts: gst::ClockTime, timeout: Duration) -> (Option<gst::Sample>, bool) {
        let deadline = std::time::Instant::now() + timeout;
        let mut dropped_late = false;
        loop {
            let left = deadline.saturating_duration_since(std::time::Instant::now());
            if left.is_zero() {
                return (None, dropped_late);
            }
            let Some(sample) = self
                .sink
                .try_pull_sample(gst::ClockTime::from_nseconds(left.as_nanos() as u64))
            else {
                return (None, dropped_late);
            };
            if sample.buffer().and_then(|buffer| buffer.pts()) == Some(PTS_BASE + pts) {
                return (Some(sample), dropped_late);
            }
            dropped_late = true;
        }
    }

    /// Asks the encoder to make the next frame pushed a keyframe, with its SPS and PPS. An encoder
    /// based on GstVideoEncoder takes an event the source sends down with that frame; Qualcomm's
    /// takes its trigger-iframe action instead. The first frame is a keyframe anyway, and
    /// qtic2venc crashes on the action before it has a frame, so before the first it does nothing.
    pub fn force_keyframe(&self) {
        if !self.started.get() {
            return;
        }
        match &self.iframe_trigger {
            Some(encoder) => {
                encoder.emit_by_name::<bool>(IFRAME_TRIGGER_SIGNAL, &[]);
            }
            None => {
                let event = gstreamer_video::DownstreamForceKeyUnitEvent::builder()
                    .all_headers(true)
                    .build();
                self.src.send_event(event);
            }
        }
    }
}

impl Drop for Pipeline {
    fn drop(&mut self) {
        let _ = self.pipeline.set_state(gst::State::Null);
    }
}

/// The message of the pipeline's latest error, if it has one.
fn bus_error(pipeline: &gst::Element) -> String {
    let Some(bus) = pipeline.bus() else {
        return String::from("no bus");
    };
    match bus.pop_filtered(&[gst::MessageType::Error]) {
        Some(message) => match message.view() {
            gst::MessageView::Error(error) => error.error().to_string(),
            _ => unreachable!("filtered for errors"),
        },
        None => String::from("no error message"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The encoder the tests use: H264_TEST_ENCODER, e.g. "qtic2venc control-rate=2" on a
    /// Tachyon, or else the first of these this machine has.
    fn test_encoder() -> Option<String> {
        const ENCODERS: [&str; 2] = [
            "x264enc tune=zerolatency speed-preset=ultrafast",
            "openh264enc",
        ];
        std::env::var("H264_TEST_ENCODER").ok().or_else(|| {
            ENCODERS
                .iter()
                .find(|encoder| {
                    let factory = encoder.split_whitespace().next().expect("not empty");
                    gst::ElementFactory::find(factory).is_some()
                })
                .map(|encoder| encoder.to_string())
        })
    }

    const WIDTH: u32 = 320;
    const HEIGHT: u32 = 240;
    const FRAMES: u64 = 10;
    /// Frames the test asks to be keyframes: the first, where nothing needs to happen, and a
    /// later one.
    const FORCED_FRAMES: [u64; 2] = [0, 5];
    const FRAME_TIMEOUT: Duration = Duration::from_secs(2);
    // 30 fps: rate control may skip frames that come faster than the frame rate says.
    const FRAME_INTERVAL: gst::ClockTime = gst::ClockTime::from_nseconds(1_000_000_000 / 30);

    #[test]
    fn a_forced_keyframe_is_the_next_frame() {
        gst::init().unwrap();
        let Some(encoder) = test_encoder() else {
            eprintln!("skipped: no H.264 encoder");
            return;
        };
        let pipeline = Pipeline::new(
            &format!(
                "appsrc name=src ! videoconvert ! {encoder} ! h264parse \
                 ! video/x-h264,stream-format=byte-stream,alignment=au ! appsink name=sink"
            ),
            &format!("video/x-raw,format=RGB,width={WIDTH},height={HEIGHT},framerate=30/1"),
        )
        .unwrap();
        let mut keyframes = Vec::new();
        for frame in 0..FRAMES {
            if FORCED_FRAMES.contains(&frame) {
                pipeline.force_keyframe();
            }
            // Each frame differs, so it is no copy of the one before.
            let pixels =
                vec![(frame * 20) as u8; (WIDTH * HEIGHT) as usize * RGB_CHANNELS as usize];
            let pts = FRAME_INTERVAL * frame;
            pipeline
                .push(gst::Buffer::from_mut_slice(pixels), pts)
                .unwrap();
            let (sample, _) = pipeline.pull(pts, FRAME_TIMEOUT);
            let buffer = sample
                .expect("every frame is encoded in time")
                .buffer_owned()
                .unwrap();
            if !buffer.flags().contains(gst::BufferFlags::DELTA_UNIT) {
                keyframes.push(frame);
            }
        }
        assert_eq!(keyframes, FORCED_FRAMES, "encoder {encoder:?}");
    }
}
