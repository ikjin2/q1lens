# Feedback and external inputs

Real experiments use several sequencers that start together, send each other measurement results and wait for triggers. This chapter covers how Q1Lens shows those interactions and how you supply the events that come from outside.

## Synchronized start

Sequencers line up at `wait_sync`. On the **Aligned** time basis, Q1Lens places every sequencer's anchor at the same point, so you can compare a drive pulse on one row with a readout window on another. Choose the anchor with [`alignment.mode`](projects.md#alignment).

To check that sequencers *stay* aligned whatever the measurement results are, see [Alignment between sequencers](verification.md#alignment-between-sequencers).

## LINQ feedback

A sequencer sends a value, and a receiver reads it with one of two instructions:

| Instruction | Behavior |
| --- | --- |
| `fb_pop_data id, Rdata` | Discards entries ahead of the requested ID, waits for it if needed, and stores its value |
| `fb_pull_data Rid, Rdata` | Takes the oldest entry, whatever its ID, and stores its ID and value |

The timeline draws a line from the sender to the receiver and marks the estimated arrival. If the receive runs before the estimated arrival, Q1Lens warns you: the Q1 core will block there, and the RT core may run out of commands while it waits.

### Where the arrival estimate comes from

Analysis estimates the arrival from the kind of data and the route:

| Route | When it applies |
| --- | --- |
| Self-cast | IDs 1–15. The value returns to the sequencer that sent it. |
| Intra-cast | IDs 16–255 between sequencers with the same `module` |
| Multi-cast | IDs 16–255 between modules |

Routes between sequencers add a transport allowance, 200 ns by default. Change it for your setup:

```yaml
analysis:
  feedback_transport_delay_ns: 200
```

The base delays for each data type are in [Event-driven simulation](../simulation.md#linq-arrival-estimates-in-analysis). In Simulation you declare each route and its full latency yourself:

```yaml
simulation:
  routes:
    - {source: sender, target: receiver, id: 16, latency_ns: 150}
```

### Balance

Q1Lens counts what each channel sends and receives. More sends than receives fill the 32-entry receive queue. A receive with no matching send has nothing to read. Both are reported.

## External inputs

Some events do not come from your Q1ASM at all: a trigger from another instrument, or a feedback word you want to assume. Supply them as external inputs. Concrete and Play use them.

### In the timeline

Select the inputs button next to **Refresh** (its tooltip is **Configure external inputs**).

- **Add trigger**: give a name, an address and the arrival times in ns.
- **Add LINQ receiver**: choose the sequencer and list one arrival per line as time, ID and word.
- **Apply & rerun** validates the inputs and runs the program again from the start.
- **Save to project** writes them to `q1timeline.yml`. **Reset changes** returns to the saved inputs.

### In the project file

```yaml
external_inputs:
  triggers:
    - id: shot_start
      address: 1
      targets: [drive, readout]
      times_ns: [1000]
  feedback:
    - target: readout
      arrivals:
        - {time_ns: 16, id: 9, value: -1}
        - {time_ns: 16, id: 5, value: 123}
```

| Key | Meaning |
| --- | --- |
| `address` | The trigger address, 1 to 15, or the name of a parameter that holds it |
| `targets` | The sequencers that receive the trigger |
| `times_ns` | Arrival times, increasing, on the shared RT clock |
| `arrivals` | Feedback words for one receiver, each with a time, an ID from 1 to 255 and a value |

## Triggers

`wait_trigger 1,2000` waits for a trigger on address 1 and then for 2000 ns more. With an arrival at 1000 ns, the wait ends at 3000 ns.

- An arrival before the wait starts does not release it.
- Q1Lens never invents a trigger. A sequencer with no matching arrival stays waiting, and the timeline says so.
- Each arrival appears as a point on the receiver's row.

`set_cond` makes the following RT commands conditional on trigger counters. The timeline shows each decision as **Execute** or **Skip**, and you can compare both.

For the exact timing rules, see [External inputs in Concrete and Play](../external-inputs.md).

## Examples

In the [tutorial examples](../../examples/q1asm-tutorials/README.md), `12-feedback-patterns` shows a balanced transfer, an early receive and queue pressure. `14-multi-sequencer-coordination` shows staggered readout and trigger skew across several rows.
