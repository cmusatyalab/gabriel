//! A run, as its TOML file describes it: the flow, and the synthetic load of each of its producers
//! and engines.

use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
    time::Duration,
};

use anyhow::{Context, Result, bail, ensure};
use gabriel::FlowSpec;
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
    /// The behavior of each of the flow's engines, by name.
    pub engines: BTreeMap<String, EngineLoad>,
}

/// How a producer publishes.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProducerLoad {
    /// Inputs per second, on average.
    pub rate: f64,
    #[serde(default)]
    pub arrivals: Arrivals,
    /// The size of each input in bytes.
    pub input_size: usize,
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

/// How a synthetic engine handles each input.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EngineLoad {
    /// How long the engine works on an input. It sleeps for that long.
    pub service_time: ServiceTime,
    /// The share of inputs the engine passes on; it stops the rest, as an early-discard filter
    /// does.
    #[serde(default = "always")]
    pub pass_probability: f64,
    /// The size of the result it reports for each input, in bytes.
    #[serde(default)]
    pub result_size: usize,
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
        }
        for (name, load) in &self.engines {
            ensure!(
                (0.0..=1.0).contains(&load.pass_probability),
                "engine {name:?} needs a pass probability between 0 and 1"
            );
            load.service_time
                .sampler()
                .with_context(|| format!("engine {name:?} has an invalid service time"))?;
        }
        Ok(())
    }
}

impl ProducerLoad {
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
