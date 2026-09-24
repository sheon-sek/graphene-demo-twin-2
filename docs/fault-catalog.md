# Fault catalog reference

Generated from `graphene_demo_twin.faults.catalog` by
`python -m graphene_demo_twin.faults.reference`; do not edit by hand.

A **Fault** is a named failure mechanism bound to an asset type. It acts only through its
mechanism: a **Physical Constraint** the simulation consumes (equipment and external
faults), an observation corruption (sensor faults), a quality change (communication faults)
or Controller misbehaviour (control faults). No fault ever writes an alarm point. Alarm bits
come from device-side logic that reads the state the fault changed.

Every fault takes the same parameters (`POST /api/faults`):

| Parameter | Meaning |
|---|---|
| `severity` | Level reached after onset, in (0, 1]; the fault's variable is `level × span`. |
| `ramp_min` | Onset: 0 is a step, otherwise a linear ramp to `severity` over this many minutes. |
| `auto_clear_min` | Duration: absent acts until Clear, otherwise the fault clears itself. |

**Targets** is the number of Plant Design assets the fault can be injected on. A fault
targets exactly the asset chosen, never a room or another asset of the type. **Spreads
along** lists the connection kinds its effect travels downstream along, which is the path a
Fault Preview and the console's propagation highlight follow.

84 faults on 40 asset types.

## BCPM

### `bcpm.breaker_trip`: Breaker trip

The breaker of the circuit this meter measures trips (once the level passes half): everything below it loses supply.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.breaker_trip` | 1 trip | 1 | 24 | power, chw, cw, air, water, fuel, fire |

### `bcpm.comm_loss`: Communication loss

The meter stops answering polls: its points go uncertain, then bad at half severity. The circuit itself carries on.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| communication | quality | `quality.comm_loss` | 1 fraction of polls lost | 1 | 24 | net |

## Breaker

### `breaker.trip`: Breaker trip

A Demo Rack breaker opens (once the level passes half): its nine circuits lose supply and the meters behind it read zero. It recloses 30 s after the Clear.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.breaker_trip` | 1 trip | 1 | 14 | power, chw, cw, air, water, fuel, fire |

### `breaker.earth_fault`: Earth fault

Leakage to earth on a Demo Rack circuit (once the level passes half): the breaker's EF bit sets and it trips on the fault, like a breaker trip.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.earth_fault` | 1 leak | 1 | 14 | power, chw, cw, air, water, fuel, fire |

## Buffer Tank

### `buffer_tank.inlet_valve_stuck`: Inlet valve stuck shut

The tank's normally open inlet valve seizes shut (once the level passes half): it reports Fail To Open, and the tank Controller opens the bypass valve so its leg keeps its flow. The tank is out of service: it stops buffering the leg and its stored charge stands.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.inlet_stuck` | 1 stuck | 1 | 8 | power, chw, cw, air, water, fuel, fire |

### `buffer_tank.bypass_valve_stuck`: Bypass valve stuck open

The tank's normally closed bypass valve seizes open (once the level passes half): it reports Fail To Close, and half the leg's flow goes round the tank, mixing unbuffered chiller water into the supply.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.bypass_stuck` | 1 stuck | 1 | 8 | power, chw, cw, air, water, fuel, fire |

## CDU

### `cdu.pump_failure`: CDU pump failure

The CDU's pump set fails (once the level passes half): it stops carrying DH08's liquid-cooled share of IT Load to the chilled water, and that heat ends up in the hall air.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.trip` | 1 fail | 1 | 3 | power, chw, cw, air, water, fuel, fire |

## CRAC

### `crac.fan_failure`: Fan failure

EC fan motor degrades and loses airflow; below 10 % airflow the unit trips on its airflow switch.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.fan_loss` | 1 fraction of airflow | 1 | 15 | power, chw, cw, air, water, fuel, fire |

### `crac.filter_choke`: Filter choke

Air filters clog and throttle airflow; the filter differential-pressure switch alarms past 25 % blockage.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.filter_blockage` | 0.6 fraction of airflow | 0.6 | 15 | power, chw, cw, air, water, fuel, fire |

### `crac.compressor_trip`: Compressor trip

The high-pressure switch trips the compressor circuit and the unit shuts down (trips once the level passes half).

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.compressor_trip` | 1 trip | 1 | 15 | power, chw, cw, air, water, fuel, fire |

### `crac.high_ambient`: High condenser ambient

Hot-air recirculation at the outdoor condenser derates the DX circuit; head pressure alarms past 40 % derate.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| external | physical_constraint | `constraint.condenser_derate` | 0.7 fraction of capacity | 0.8 | 15 | power, chw, cw, air, water, fuel, fire |

### `crac.setpoint_drift`: Setpoint drift

The unit controller's supply-air setpoint drifts upwards, so it unloads the compressor while the equipment is healthy and raises no alarm.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| control | controller | `controller.setpoint_offset_c` | 8 °C | 1 | 15 | power, chw, cw, air, water, fuel, fire |

### `crac.compressor_failure`: Compressor failure

The lead compressor's circuit locks out on its high-pressure switch (once the level passes half): the unit keeps running on its lag compressor, which carries under a third of its capacity, so the hall warms.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.compressor_loss` | 1 loss | 1 | 15 | power, chw, cw, air, water, fuel, fire |

### `crac.comm_loss`: Communication loss

The unit stops answering BMS polls: its points go uncertain, then bad at half severity, and the supervisor raises Loss of Signal.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| communication | quality | `quality.comm_loss` | 1 fraction of polls lost | 1 | 15 | net |

## CW Ground Valve

### `water.municipal_loss`: Municipal supply loss

The mains supply to the site fails: the ground tanks stop refilling and drain as the transfer and AC makeup pumps draw on them.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| external | physical_constraint | `constraint.supply_loss` | 1 fraction of mains flow lost | 1 | 1 | power, chw, cw, air, water, fuel, fire |

### `ground_valve.stuck`: Valve stuck

A ground tank inlet valve's actuator seizes and it holds its position whatever it is commanded (once the level passes half): the tank stops floating on level.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.stuck` | 1 stuck | 1 | 2 | power, chw, cw, air, water, fuel, fire |

## CW Roof Tank

### `roof_tank.level_sensor`: Level sensor reads high

The roof tank's level transmitter reads high. The water is unchanged, but the transfer pumps, controlled on the reading, start late or not at all.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| sensor | observation | `observation.level_offset_pct` | 40 % of level | 1 | 2 | nothing (the world is unchanged) |

## CW Roof Valve

### `roof_valve.stuck`: Valve stuck

A roof tank inlet valve's actuator seizes and it holds its position whatever it is commanded (once the level passes half): stuck open, it can overfill its tank.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.stuck` | 1 stuck | 1 | 2 | power, chw, cw, air, water, fuel, fire |

## CW Transfer Pump

### `transfer_pump.trip`: Transfer pump trip

The pump trips on overload (once the level passes half). The standby pump takes its place; with all three lost the roof tanks drain at what the towers evaporate.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.trip` | 1 trip | 1 | 3 | power, chw, cw, air, water, fuel, fire |

## Ceiling Cooling Units

### `ccu.fan_failure`: Fan failure

The supply fan loses airflow: the unit delivers less cooling to its zone, and below half airflow its differential-pressure switch alarms.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.fan_loss` | 1 fraction of airflow | 1 | 8 | power, chw, cw, air, water, fuel, fire |

### `ccu.filter_choke`: Filter choke

Air filters clog and throttle airflow; the filter switch alarms past 25 % blockage.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.filter_blockage` | 0.6 fraction of airflow | 0.6 | 8 | power, chw, cw, air, water, fuel, fire |

## Chiller

### `chiller.trip`: Chiller trip

A compressor fault trips the chiller on its safety chain (once the level passes half). The trip latches until Reset or 15 minutes after Clear; the sequencer starts the next chiller at once, and with none left the chilled water warms.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.trip` | 1 trip | 1 | 4 | power, chw, cw, air, water, fuel, fire |

### `chiller.compressor_degradation`: Compressor degradation

Worn impeller and leaking guide vanes: the compressor loses up to half its capacity and a third of its efficiency, so it draws more power per kW of cooling and, loaded up, cannot hold the chilled-water setpoint.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.compressor_degradation` | 1 fraction worn | 0.6 | 4 | power, chw, cw, air, water, fuel, fire |

### `chiller.condenser_fouling`: Condenser fouling

Scale on the condenser tubes widens the condensing approach: condenser pressure, discharge temperature and power rise at the same load, and past 1,200 kPa the chiller unloads.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.condenser_fouling` | 1 fraction fouled | 0.5 | 4 | power, chw, cw, air, water, fuel, fire |

### `chiller.chws_sensor_drift`: CHW sensor drift

The chiller's leaving chilled-water sensor drifts high the longer the fault acts, until it saturates; Clear recalibrates it. The water itself, the header sensors and the units downstream are unchanged, so the reading disagrees with them.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| sensor | observation | `observation.drift_c_per_h` | 4 °C per hour | 0.5 | 4 | nothing (the world is unchanged) |

### `chiller.hand_mode`: Left in hand mode

The chiller's selector is left in hand and on (once the level passes half): it runs whatever the demand, outside the sequencer, which stops an auto chiller to make room. Surplus primary flow goes round the bypass and the plant draws more power. The equipment is healthy and raises no alarm.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| control | controller | `controller.hand_mode` | 1 hand | 1 | 4 | power, chw, cw, air, water, fuel, fire |

## Chiller Pump

### `pump.trip`: Pump trip

The pump's motor overload trips (once the level passes half). A leg pump takes its chiller out of service and the sequencer starts the next; the secondary pump stops the chilled water to every air unit.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.trip` | 1 trip | 1 | 9 | power, chw, cw, air, water, fuel, fire |

### `pump.bearing_wear`: Bearing wear

Worn bearings add friction: the pump draws up to 30 % more power for the same speed and flow.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.bearing_wear` | 1 fraction worn | 0.6 | 9 | power, chw, cw, air, water, fuel, fire |

### `pump.dp_pid_oscillation`: DP PID oscillation

The DP PID driving the secondary pump is mistuned to up to five times its gains: the pump speed hunts and the header differential pressure swings round its setpoint, while the equipment is healthy and raises no alarm.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| control | controller | `controller.gain_factor` | 4 gain increase | 1 | 1 | power, chw, cw, air, water, fuel, fire |

## Chiller Valve

### `valve.stuck`: Valve stuck

The actuator seizes (once the level passes half) and the valve stays at the % open it was: its position no longer follows its command. A leg valve stuck short of open takes its chiller out of service; a stuck bypass valve leaves the others to carry the bypass PID.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.stuck` | 1 stuck | 1 | 16 | power, chw, cw, air, water, fuel, fire |

## Cooling Tower

### `tower.fan_failure`: Tower fan failure

The cell's fan loses airflow (a slipping belt, then a failed gearbox) and trips past 90 %. The other cells in its Tower Group speed up; if they cannot make up for it, condenser water warms, and with it condenser pressure and chiller power.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.fan_loss` | 1 fraction of airflow | 1 | 20 | power, chw, cw, air, water, fuel, fire |

### `tower.group_fan_failure`: Tower Group fan failure

The starter panel feeding the fans of the cell's whole Tower Group fails: every cell loses airflow and trips past 90 %. Condenser water warms, then condenser pressure and chiller power, until the chiller's high-pressure trip hands its load to another; the sequencer starts no chiller on the group while it acts.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.group_fan_loss` | 1 fraction of airflow | 1 | 20 | power, chw, cw, air, water, fuel, fire |

### `tower.fill_fouling`: Fill fouling

Scale and biofilm on the fill halve its heat transfer at full severity: the cell rejects less heat, so its group's fans run faster to hold condenser water.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.fill_fouling` | 1 fraction fouled | 0.6 | 20 | power, chw, cw, air, water, fuel, fire |

## Diesel

### `diesel.fuel_pump_failure`: Fuel pump failure

The tank's transfer pump trips: while its gensets run, their day tanks drain and are not refilled, until the engines shut down on low fuel.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.pump_failure` | 1 fail | 1 | 3 | power, chw, cw, air, water, fuel, fire |

## Environment Monitoring

### `em.offset`: Sensor offset

The temperature element reads high by a fixed amount (a bad calibration or a loose termination); the cold-aisle air itself is unchanged.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| sensor | observation | `observation.offset_c` | 5 °C | 0.6 | 168 | nothing (the world is unchanged) |

### `em.drift`: Sensor drift

The temperature element drifts further high the longer the fault acts, until it saturates; Clear recalibrates it. The cold-aisle air itself is unchanged.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| sensor | observation | `observation.drift_c_per_h` | 4 °C per hour | 0.5 | 168 | nothing (the world is unchanged) |

### `em.stuck`: Stuck reading

The sensor freezes on its last temperature and humidity readings (once the level passes half).

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| sensor | observation | `observation.stuck` | 1 stuck | 1 | 168 | nothing (the world is unchanged) |

## FCU

### `fcu.fan_failure`: Fan failure

The supply fan loses airflow: the unit delivers less cooling to its zone, and below half airflow its differential-pressure switch alarms.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.fan_loss` | 1 fraction of airflow | 1 | 5 | power, chw, cw, air, water, fuel, fire |

### `fcu.filter_choke`: Filter choke

Air filters clog and throttle airflow; the filter switch alarms past 25 % blockage.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.filter_blockage` | 0.6 fraction of airflow | 0.6 | 5 | power, chw, cw, air, water, fuel, fire |

## FWU

### `fwu.fan_failure`: Fan failure

The supply fan loses airflow: the unit delivers less cooling to its zone, and below half airflow its differential-pressure switch alarms.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.fan_loss` | 1 fraction of airflow | 1 | 10 | power, chw, cw, air, water, fuel, fire |

### `fwu.filter_choke`: Filter choke

Air filters clog and throttle airflow; the filter switch alarms past 25 % blockage.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.filter_blockage` | 0.6 fraction of airflow | 0.6 | 10 | power, chw, cw, air, water, fuel, fire |

## Fire Zone

### `fire.room_fire`: Fire

A fire breaks out in the zone's room: smoke fills it and the ceiling heats, its detectors alarm, the zone's fresh-air handlers shut down and the lifts return to the ground floor. In a sprinklered zone the heads open, the alarm valve flows, the fire pumps start and the fire is knocked down. The fire's heat and the lost fresh air warm and humidify the room.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| external | physical_constraint | `constraint.fire_kw` | 250 kW | 1 | 22 | power, chw, cw, air, water, fuel, fire |

## GEM230

### `gem230.breaker_trip`: Breaker trip

The breaker of the circuit this meter measures trips (once the level passes half): everything below it loses supply.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.breaker_trip` | 1 trip | 1 | 3 | power, chw, cw, air, water, fuel, fire |

### `gem230.comm_loss`: Communication loss

The meter stops answering polls: its points go uncertain, then bad at half severity. The circuit itself carries on.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| communication | quality | `quality.comm_loss` | 1 fraction of polls lost | 1 | 3 | net |

## GEM630

### `ats.fail_to_transfer`: ATS fails to transfer

The main bus's ATS mechanism jams: it stays where it is, so on a utility loss the bus stays dead while its gensets run, and the UPS batteries run down.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.ats_stuck` | 1 stuck | 1 | 2 | power, chw, cw, air, water, fuel, fire |

### `gem630.breaker_trip`: Breaker trip

The breaker of the circuit this meter measures trips (once the level passes half): everything below it loses supply.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.breaker_trip` | 1 trip | 1 | 2 | power, chw, cw, air, water, fuel, fire |

### `gem630.comm_loss`: Communication loss

The meter stops answering polls: its points go uncertain, then bad at half severity. The circuit itself carries on.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| communication | quality | `quality.comm_loss` | 1 fraction of polls lost | 1 | 2 | net |

## GPM96

### `gpm96.breaker_trip`: Breaker trip

The breaker of the circuit this meter measures trips (once the level passes half): everything below it loses supply.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.breaker_trip` | 1 trip | 1 | 28 | power, chw, cw, air, water, fuel, fire |

### `gpm96.comm_loss`: Communication loss

The meter stops answering polls: its points go uncertain, then bad at half severity. The circuit itself carries on.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| communication | quality | `quality.comm_loss` | 1 fraction of polls lost | 1 | 28 | net |

## GPQM144

### `utility.incomer_loss`: Utility incomer loss

The grid supply on this incomer fails (a voltage sag below half severity). The other transformer on the side carries its MSB; with both gone the ATS starts the gensets and transfers, and the UPS batteries bridge the gap.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| external | physical_constraint | `constraint.utility_loss` | 1 loss | 1 | 4 | power, chw, cw, air, water, fuel, fire |

### `gpqm144.breaker_trip`: Breaker trip

The breaker of the circuit this meter measures trips (once the level passes half): everything below it loses supply.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.breaker_trip` | 1 trip | 1 | 10 | power, chw, cw, air, water, fuel, fire |

### `gpqm144.comm_loss`: Communication loss

The meter stops answering polls: its points go uncertain, then bad at half severity. The circuit itself carries on.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| communication | quality | `quality.comm_loss` | 1 fraction of polls lost | 1 | 10 | net |

## GPQM96

### `gpqm96.breaker_trip`: Breaker trip

The breaker of the circuit this meter measures trips (once the level passes half): everything below it loses supply.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.breaker_trip` | 1 trip | 1 | 3 | power, chw, cw, air, water, fuel, fire |

### `gpqm96.comm_loss`: Communication loss

The meter stops answering polls: its points go uncertain, then bad at half severity. The circuit itself carries on.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| communication | quality | `quality.comm_loss` | 1 fraction of polls lost | 1 | 3 | net |

## Genset

### `genset.fail_to_start`: Fail to start

The engine cranks but does not fire, and shuts down on over-crank; the other gensets on its bus (N+1) carry the load.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.fail_to_start` | 1 fail | 1 | 6 | power, chw, cw, air, water, fuel, fire |

## Heat Detector

### `heat_detector.fault`: Detector fault

The detector fails (a dirty chamber, a broken head): the panel shows it in fault, and it no longer detects a fire in its zone.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.detector_fault` | 1 fault | 1 | 7 | power, chw, cw, air, water, fuel, fire |

### `heat_detector.false_alarm`: False alarm

The device reports a fire that is not there (dust or steam in a detector, a call point knocked): the zone goes into alarm, its fresh-air handlers shut down and the lifts are recalled, while the air itself is clean.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| sensor | observation | `observation.false_alarm` | 1 alarm | 1 | 7 | nothing (the world is unchanged) |

## IPS

### `ips.insulation_fault`: Insulation fault

The isolated circuit's insulation to earth breaks down (damp, damaged cable): it falls from 10 MΩ towards 5 kΩ. The IPS insulation monitor reads every circuit in parallel and its value drops; below 50 kΩ the fault locator flags this circuit. The isolated supply keeps running, as an IT system is meant to on a first fault.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.insulation_loss` | 1 fraction of insulation lost | 1 | 6 | power, chw, cw, air, water, fuel, fire |

### `ips.ct_open`: Locator CT open

The fault locator's current transformer on this circuit is disconnected (once the level passes half): the IPS raises No CT and can no longer locate an insulation fault on the circuit, although the monitor still measures it.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| sensor | observation | `observation.ct_open` | 1 open | 1 | 6 | nothing (the world is unchanged) |

### `ips.ct_short`: Locator CT short-circuited

The fault locator's current transformer on this circuit is short-circuited (once the level passes half): the IPS raises Short CT and can no longer locate an insulation fault on the circuit.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| sensor | observation | `observation.ct_short` | 1 short | 1 | 6 | nothing (the world is unchanged) |

### `ips.pe_loss`: PE connection lost

The protective-earth bond of this circuit's panel section opens (once the level passes half): the insulation monitor loses its reference to earth, raises PE Connection and cannot measure insulation until it is restored.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.pe_loss` | 1 lost | 1 | 6 | power, chw, cw, air, water, fuel, fire |

## IT Load

### `it.load_surge`: IT load surge

Tenant workload surges in one Data Hall, up to its design load. All of it becomes heat in that hall, and the UPS and transformers carry more.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| external | physical_constraint | `constraint.load_surge` | 0.35 fraction of design load | 1 | 8 | power, chw, cw, air, water, fuel, fire |

## Lift

### `lift.stuck`: Lift stuck

The car stops where it is, between floors if it was travelling, with its doors shut; on Clear it carries on to the landing it was heading for.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.stuck` | 1 stuck | 1 | 3 | power, chw, cw, air, water, fuel, fire |

## Makeup Water Pump

### `makeup_pump.failure`: Makeup pump failure

The cell's makeup pump trips (once the level passes half): its basin falls with evaporation until the cell trips on low basin level.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.trip` | 1 trip | 1 | 20 | power, chw, cw, air, water, fuel, fire |

## Manual Call Point

### `call_point.false_alarm`: False alarm

The device reports a fire that is not there (dust or steam in a detector, a call point knocked): the zone goes into alarm, its fresh-air handlers shut down and the lifts are recalled, while the air itself is clean.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| sensor | observation | `observation.false_alarm` | 1 alarm | 1 | 9 | nothing (the world is unchanged) |

## Network Device

### `network.device_down`: Device down

The device stops forwarding: it and everything the supervisor reaches only through it lose quality.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| communication | quality | `quality.comm_loss` | 1 fraction of packets lost | 1 | 20 | net |

### `network.switch_failure`: Switch failure

The switch dies (once the level passes half): its links go down, and every device and field point the supervisor reaches only through it goes Bad and stale. The equipment behind it keeps running. On Clear it reboots.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| communication | quality | `quality.device_failure` | 1 fail | 1 | 4 | net |

### `network.gateway_failure`: Gateway failure

The field gateway dies (once the level passes half): every point of the systems it carries goes Bad and stale, while the equipment keeps running. On Clear it reboots.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| communication | quality | `quality.device_failure` | 1 fail | 1 | 2 | net |

### `network.server_overload`: Server overload

A runaway process eats the server's spare CPU and memory: it answers slowly and warns. A SCADA server past 95 % CPU overruns its scan, so what only it serves goes Uncertain; its redundant partner keeps the rest good.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.cpu_overload` | 1 share of spare CPU consumed | 1 | 3 | power, chw, cw, air, water, fuel, fire |

### `network.ntp_drift`: NTP drift

The time server's clock drifts, growing until Clear resynchronises it; it warns once it is a second out.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| sensor | observation | `observation.clock_drift_s_per_h` | 60 s/h | 1 | 1 | nothing (the world is unchanged) |

## Network Switch

### `network.port_flap`: Port flap

The switch's first uplink port flaps: its link drops for part of every 20 s, errors climb, the switch works harder, and everything reached through the link turns Uncertain.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| communication | quality | `quality.port_flap` | 1 fraction of each cycle down | 0.5 | 4 | net |

## PAHU

### `pahu.fan_failure`: Fan failure

The supply fan loses airflow: the unit delivers less cooling to its zone, and below half airflow its differential-pressure switch alarms.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.fan_loss` | 1 fraction of airflow | 1 | 15 | power, chw, cw, air, water, fuel, fire |

### `pahu.filter_choke`: Filter choke

Air filters clog and throttle airflow; the filter switch alarms past 25 % blockage.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.filter_blockage` | 0.6 fraction of airflow | 0.6 | 15 | power, chw, cw, air, water, fuel, fire |

## Production/E820

### `e820.incomer_trip`: Incomer trip

The Demo Rack's own incomer trips (once the level passes half): every circuit and demo meter loses supply. The site is unaffected.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.incomer_trip` | 1 trip | 1 | 1 | power, chw, cw, air, water, fuel, fire |

## Smoke Detector

### `smoke_detector.fault`: Detector fault

The detector fails (a dirty chamber, a broken head): the panel shows it in fault, and it no longer detects a fire in its zone.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.detector_fault` | 1 fault | 1 | 29 | power, chw, cw, air, water, fuel, fire |

### `smoke_detector.false_alarm`: False alarm

The device reports a fire that is not there (dust or steam in a detector, a call point knocked): the zone goes into alarm, its fresh-air handlers shut down and the lifts are recalled, while the air itself is clean.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| sensor | observation | `observation.false_alarm` | 1 alarm | 1 | 29 | nothing (the world is unchanged) |

## Temperature and Humidity

### `th.offset`: Sensor offset

The temperature element reads high by a fixed amount (a bad calibration or a loose termination); the hot-aisle air itself is unchanged.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| sensor | observation | `observation.offset_c` | 5 °C | 0.6 | 64 | nothing (the world is unchanged) |

### `th.drift`: Sensor drift

The temperature element drifts further high the longer the fault acts, until it saturates; Clear recalibrates it. The hot-aisle air itself is unchanged.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| sensor | observation | `observation.drift_c_per_h` | 4 °C per hour | 0.5 | 64 | nothing (the world is unchanged) |

### `th.stuck`: Stuck reading

The sensor freezes on its last temperature and humidity readings (once the level passes half).

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| sensor | observation | `observation.stuck` | 1 stuck | 1 | 64 | nothing (the world is unchanged) |

### `th.comm_loss`: Communication loss

The sensor stops answering polls: uncertain, then bad at half severity.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| communication | quality | `quality.comm_loss` | 1 fraction of polls lost | 1 | 64 | net |

## UPS

### `ups.rectifier_failure`: Rectifier failure

The rectifier fails: the module runs on its battery until it is exhausted, then drops out and the other two modules in the hall take its share.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.rectifier_failure` | 1 fail | 1 | 25 | power, chw, cw, air, water, fuel, fire |

### `ups.battery_degradation`: Battery degradation

The battery strings age and lose capacity: nothing shows until the module is on battery, when its charge falls faster and its autonomy is shorter.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.battery_fade` | 0.8 fraction of capacity lost | 0.5 | 25 | power, chw, cw, air, water, fuel, fire |

## Water Leak Cable Sensor

### `leak.pipe_leak`: Pipe leak

A pipe leaks in the room this cable runs under. Water pools on the floor and the cable alarms, with its position, once the water reaches it; the closed CHW loop (or, in a water plant room, the tanks) loses the water, until it runs dry.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| equipment | physical_constraint | `constraint.leak_lps` | 2 L/s | 1 | 18 | power, chw, cw, air, water, fuel, fire |

## Weather Station

### `weather.high_wet_bulb`: High wet bulb

A humid spell raises the outdoor wet bulb above the season's: the towers reject heat less readily, the chillers lift harder and PUE rises.

| Category | Mechanism | Variable | At severity 1 | Default severity | Targets | Spreads along |
|---|---|---|---|---|---|---|
| external | physical_constraint | `constraint.wet_bulb_rise_c` | 4 °C | 0.75 | 1 | power, chw, cw, air, water, fuel, fire |
