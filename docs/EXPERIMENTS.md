# Experiment guide

PTPBox is designed for repeatable comparisons, not just attractive live traces.
An experiment should capture topology, software versions, message profile,
servo parameters, stimulus, duration, and analysis window together.

## Recommended baseline

Before injecting a disturbance:

1. let every clock report stable lock for at least five minutes;
2. record NIC firmware, driver, LinuxPTP version, and PHC mapping;
3. capture 120 seconds of undisturbed offset;
4. save the active PTP profile and PI constants;
5. use the same physical cabling and link rates for every comparison.

## Built-in recipes

### Servo step response

Apply a discrete phase step at one boundary clock and measure:

- rise and settling time;
- overshoot and ringing;
- downstream amplification;
- lock-state transitions;
- recovery of RMS and MTIE.

The default design uses +1 μs at BC3 with 10 seconds of pre-trigger and 110
seconds post-trigger.

### Low-frequency wander

Introduce a slow phase or frequency modulation to test integral tracking and
downstream correlation. Use a capture long enough to include several complete
cycles; 20 minutes is a useful starting point.

### Holdover recovery

Remove upstream synchronization, observe holdover drift, restore the source,
and measure reacquisition. Record both the duration of source loss and whether
the clock stepped or slewed on recovery.

### Gain sweep

Run a matrix of proportional and integral constants with an identical stimulus.
Compare settling time, overshoot, steady-state RMS, and worst downstream MTIE.

## Servo parameters

| Parameter | Effect | Trade-off |
| --- | --- | --- |
| `Kp` | Immediate frequency correction from phase error | Faster response can increase overshoot/noise sensitivity |
| `Ki` | Accumulated correction for persistent error | Strong drift rejection can reduce phase margin |
| `step_threshold` | Selects step versus slew during normal operation | Steps settle quickly but break phase continuity |
| `first_step_threshold` | Allows a larger correction during initial lock | Too low can prolong acquisition |
| `sanity_freq_limit` | Rejects implausible frequency corrections | Too tight can reject legitimate acquisition transients |

## Analysis windows

- **Current offset:** operational state, not a stability statistic.
- **RMS:** useful for comparing noise energy over an identical window.
- **P95 / peak:** captures excursions that RMS can hide.
- **MTIE:** bounds peak-to-peak time-error growth across observation intervals.
- **TDEV:** separates time stability across averaging intervals.
- **Allan deviation:** characterizes oscillator/frequency noise processes.

Only RMS, density, P95, and MTIE presentation are currently built into the UI.
Direct TDEV and Allan-deviation computation are planned.

## Result naming

Use a compact name that captures the manipulated variable:

```text
2026-07-17_step-bc3_kp070_ki030_run01
```

Keep raw logs immutable. Put derived CSV/plots beside them with a suffix such as
`_summary`, `_mtie`, or `_filtered`.

## Comparing runs

Change one independent variable at a time. If a cable, firmware, message rate,
or link speed changes, treat it as a new experiment family. Report the endpoint
metric and the per-hop contribution; an apparently improved OC result can hide
instability that moved upstream.
