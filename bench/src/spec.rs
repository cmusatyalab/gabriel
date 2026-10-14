//! A run, as its TOML file describes it: the flow, and the synthetic load of each of its producers
//! and engines.

use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
    time::Duration,
};

use anyhow::{Context, Result, bail, ensure};
use gabriel::{FlowSpec, client::DEFAULT_REMOTE_CROSSING_CAPACITY};
use rand::{Rng, distr::Uniform};
use rand_distr::{Distribution, Exp, LogNormal};
use serde::Deserialize;

/// One benchmark run.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunSpec {
    /// Names the flow, and the run's output directory.
    pub name: String,
    /// How long the producers publish.
    #[serde(with = "humantime_serde")]
    pub duration: Duration,
    /// How long the run keeps recording after the producers stop, so that the inputs in flight
    /// end.
    #[serde(with = "humantime_serde")]
    pub drain: Duration,
    /// Seeds every random choice, so a run can be repeated.
    pub seed: u64,
    pub flow: FlowSpec,
    /// The load of each of the flow's producers, by name.
    pub producers: BTreeMap<String, ProducerLoad>,
    /// The behavior of each of the flow's engines, by name. External engines are listed too.
    pub engines: BTreeMap<String, EngineLoad>,
    /// How the client uses the remote server, if the flow has remote engines.
    #[serde(default)]
    pub remote: RemoteSettings,
    /// The link to the remote server, emulated with netem. Without it, the link is loopback.
    pub network: Option<NetworkSpec>,
}

/// How the client uses the remote server.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RemoteSettings {
    /// How many inputs each crossing to the remote server may have in flight.
    pub crossing_capacity: usize,
}

impl Default for RemoteSettings {
    fn default() -> Self {
        Self {
            crossing_capacity: DEFAULT_REMOTE_CROSSING_CAPACITY,
        }
    }
}

/// An emulated link. Both directions get the same settings.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NetworkSpec {
    /// The delay each way, so the round trip takes twice as long.
    #[serde(with = "humantime_serde")]
    pub delay: Duration,
    /// How much the delay varies, up or down.
    #[serde(default, with = "humantime_serde")]
    pub jitter: Duration,
    /// The share of packets lost each way, from 0 to 1.
    #[serde(default)]
    pub loss: f64,
    /// The bandwidth each way, in tc's syntax, e.g. "20mbit". Unlimited if not given.
    pub rate: Option<String>,
}

/// How a producer publishes.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProducerLoad {
    /// Inputs per second, on average.
    pub rate: f64,
    #[serde(default)]
    pub arrivals: Arrivals,
    /// The size of each input in bytes, for inputs of zeros. Not given with `frames`.
    #[serde(default)]
    pub input_size: usize,
    /// A directory of files, e.g. JPEG frames, that the producer publishes in name order, over
    /// and over, in place of zeros. Relative to the working directory.
    pub frames: Option<PathBuf>,
    /// How engines read the inputs, e.g. "steeleagle_protocol.v1.messages.stream.RawFrame" for
    /// the frames `raw_frames.py` writes. None if they have no convention.
    pub input_type: Option<String>,
}

/// When a producer's inputs are published.
#[derive(Debug, Default, Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Arrivals {
    /// At a fixed period, as a camera does.
    #[default]
    Periodic,
    /// At random, with exponential gaps.
    Poisson,
}

/// How an engine handles each input: a synthetic engine that the bench runs, or an external one.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EngineLoad {
    /// The engine runs in its own process, e.g. a real model, and the bench only adds it to its
    /// server. Start it before the run. Of the other fields, only `accelerator` applies to it.
    #[serde(default)]
    pub external: bool,
    /// How long a synthetic engine works on an input. It sleeps for that long.
    pub service_time: Option<ServiceTime>,
    /// The share of inputs the engine passes on; it stops the rest, as an early-discard filter
    /// does.
    #[serde(default = "always")]
    pub pass_probability: f64,
    /// The size of the result it reports for each input, in bytes.
    #[serde(default)]
    pub result_size: usize,
    /// The accelerator the engine works on, e.g. "npu". Engines that name the same one take turns
    /// on it, as engines sharing one GPU do, so an input waits for the accelerator after it is
    /// sent, and that wait counts in its service time. An engine without one has its own. For an
    /// external engine, it only tells the analysis; the hardware makes the engines take turns.
    pub accelerator: Option<String>,
}

fn always() -> f64 {
    1.0
}

/// The distribution of an engine's time per input.
#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(tag = "distribution", rename_all = "lowercase", deny_unknown_fields)]
pub enum ServiceTime {
    Constant {
        #[serde(with = "humantime_serde")]
        time: Duration,
    },
    Uniform {
        #[serde(with = "humantime_serde")]
        min: Duration,
        #[serde(with = "humantime_serde")]
        max: Duration,
    },
    Exponential {
        #[serde(with = "humantime_serde")]
        mean: Duration,
    },
    /// A heavy tail, with the given mean and standard deviation.
    LogNormal {
        #[serde(with = "humantime_serde")]
        mean: Duration,
        #[serde(with = "humantime_serde")]
        sd: Duration,
    },
}

impl RunSpec {
    /// Reads and checks the run described by the TOML file at `path`. Returns the run and the
    /// file's text, which is kept with the results.
    pub fn read(path: &Path) -> Result<(Self, String)> {
        let text = std::fs::read_to_string(path)
            .with_context(|| format!("failed to read {}", path.display()))?;
        let spec: Self =
            toml::from_str(&text).with_context(|| format!("invalid run {}", path.display()))?;
        spec.check()?;
        Ok((spec, text))
    }

    /// Checks that every producer and engine of the flow has a load, and that each load makes
    /// sense. The flow itself is checked when the client adds it.
    fn check(&self) -> Result<()> {
        let flow_producers: BTreeSet<_> = self.flow.producers.iter().map(|p| &p.name).collect();
        ensure!(
            flow_producers == self.producers.keys().collect(),
            "every producer of the flow needs a load in [producers], and only those"
        );
        let flow_engines: BTreeSet<_> = self.flow.engines.iter().map(|e| &e.name).collect();
        ensure!(
            flow_engines == self.engines.keys().collect(),
            "every engine of the flow needs a load in [engines], and only those"
        );
        for (name, load) in &self.producers {
            ensure!(
                load.rate.is_finite() && load.rate > 0.0,
                "producer {name:?} needs a positive rate"
            );
            ensure!(
                load.frames.is_some() != (load.input_size > 0),
                "producer {name:?} needs either an input size or frames"
            );
        }
        ensure!(
            self.remote.crossing_capacity > 0,
            "remote crossings need a capacity of at least 1"
        );
        if let Some(network) = &self.network {
            ensure!(
                (0.0..1.0).contains(&network.loss),
                "the network's loss must be at least 0 and below 1"
            );
        }
        for (name, load) in &self.engines {
            ensure!(
                (0.0..=1.0).contains(&load.pass_probability),
                "engine {name:?} needs a pass probability between 0 and 1"
            );
            if load.external {
                ensure!(
                    load.service_time.is_none(),
                    "external engine {name:?} does its own work, so it has no service time"
                );
            } else {
                load.service_time()
                    .with_context(|| format!("engine {name:?} has an invalid service time"))?;
            }
        }
        Ok(())
    }
}

impl EngineLoad {
    /// How long a synthetic engine works on each input.
    pub fn service_time(&self) -> Result<Sampler> {
        self.service_time
            .context("a synthetic engine needs a service time")?
            .sampler()
    }
}

impl ProducerLoad {
    /// What the producer publishes, in turn: the files in `frames`, or one input of zeros.
    pub fn payloads(&self) -> Result<Vec<Vec<u8>>> {
        let Some(frames) = &self.frames else {
            return Ok(vec![vec![0; self.input_size]]);
        };
        let mut paths = Vec::new();
        for entry in std::fs::read_dir(frames)
            .with_context(|| format!("failed to read frames from {}", frames.display()))?
        {
            let path = entry?.path();
            if path.is_file() {
                paths.push(path);
            }
        }
        ensure!(!paths.is_empty(), "no frames in {}", frames.display());
        paths.sort();
        paths
            .iter()
            .map(|path| {
                std::fs::read(path).with_context(|| format!("failed to read {}", path.display()))
            })
            .collect()
    }

    /// The gaps between inputs.
    pub fn gaps(&self) -> Result<Sampler> {
        let mean = Duration::from_secs_f64(self.rate.recip());
        match self.arrivals {
            Arrivals::Periodic => ServiceTime::Constant { time: mean },
            Arrivals::Poisson => ServiceTime::Exponential { mean },
        }
        .sampler()
    }
}

/// Draws durations from a distribution.
#[derive(Debug, Clone, Copy)]
pub enum Sampler {
    Constant(Duration),
    Uniform(Uniform<f64>),
    Exponential(Exp<f64>),
    LogNormal(LogNormal<f64>),
}

impl Sampler {
    pub fn sample(&self, rng: &mut impl Rng) -> Duration {
        let seconds = match self {
            Self::Constant(time) => return *time,
            Self::Uniform(distribution) => distribution.sample(rng),
            Self::Exponential(distribution) => distribution.sample(rng),
            Self::LogNormal(distribution) => distribution.sample(rng),
        };
        Duration::from_secs_f64(seconds)
    }
}

impl ServiceTime {
    pub fn sampler(&self) -> Result<Sampler> {
        Ok(match *self {
            Self::Constant { time } => Sampler::Constant(time),
            Self::Uniform { min, max } => {
                ensure!(min <= max, "min is above max");
                Sampler::Uniform(Uniform::new_inclusive(
                    min.as_secs_f64(),
                    max.as_secs_f64(),
                )?)
            }
            Self::Exponential { mean } => {
                ensure!(!mean.is_zero(), "the mean must be positive");
                Sampler::Exponential(Exp::new(mean.as_secs_f64().recip())?)
            }
            Self::LogNormal { mean, sd } => {
                if mean.is_zero() || sd.is_zero() {
                    bail!("the mean and the standard deviation must be positive");
                }
                // The log-normal whose mean is m and standard deviation s has
                // sigma^2 = ln(1 + s^2 / m^2) and mu = ln(m) - sigma^2 / 2.
                let (m, s) = (mean.as_secs_f64(), sd.as_secs_f64());
                let variance = (s / m).powi(2).ln_1p();
                Sampler::LogNormal(LogNormal::new(m.ln() - variance / 2.0, variance.sqrt())?)
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use rand::{SeedableRng, rngs::StdRng};

    use super::*;

    const SEED: u64 = 7;
    const SAMPLES: u32 = 100_000;
    /// How far a sample mean may be from the mean it estimates, as a share of it.
    const TOLERANCE: f64 = 0.02;

    fn mean_of(sampler: Sampler) -> Duration {
        let mut rng = StdRng::seed_from_u64(SEED);
        (0..SAMPLES)
            .map(|_| sampler.sample(&mut rng))
            .sum::<Duration>()
            / SAMPLES
    }

    fn close(actual: Duration, expected: Duration) -> bool {
        (actual.as_secs_f64() / expected.as_secs_f64() - 1.0).abs() < TOLERANCE
    }

    #[test]
    fn log_normal_has_the_mean_it_was_given() {
        let mean = Duration::from_millis(20);
        let sampler = ServiceTime::LogNormal {
            mean,
            sd: Duration::from_millis(30),
        }
        .sampler()
        .unwrap();

        assert!(close(mean_of(sampler), mean));
    }

    #[test]
    fn poisson_arrivals_have_the_rate_they_were_given() {
        let load = ProducerLoad {
            rate: 50.0,
            arrivals: Arrivals::Poisson,
            input_size: 1,
            frames: None,
            input_type: None,
        };

        assert!(close(
            mean_of(load.gaps().unwrap()),
            Duration::from_millis(20)
        ));
    }

    #[test]
    fn uniform_with_min_above_max_is_rejected() {
        let time = ServiceTime::Uniform {
            min: Duration::from_millis(2),
            max: Duration::from_millis(1),
        };

        assert!(time.sampler().is_err());
    }

    #[test]
    fn example_runs_are_valid() {
        let runs = Path::new(env!("CARGO_MANIFEST_DIR")).join("runs");
        for entry in std::fs::read_dir(runs).unwrap() {
            let path = entry.unwrap().path();
            RunSpec::read(&path).unwrap_or_else(|e| panic!("{}: {e:#}", path.display()));
        }
    }
}
