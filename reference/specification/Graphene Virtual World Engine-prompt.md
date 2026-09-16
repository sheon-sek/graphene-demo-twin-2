# Graphene Virtual World Engine

## Greenfield Master Build Specification v2

你现在处于一个**全新的、空白 Repository**。

不要假设存在任何旧代码、旧 branch、旧 package、旧 API、旧目录结构、旧 Admin Panel、旧 commit 或旧实现需要兼容。

你的任务是：

> 从零构建一个完整、工程上自洽、确定性可重放、支持 Demo 与无限 Open World、支持运行时故障注入，并具有专业 React Admin + 3D Digital Twin 的 Graphene Virtual World Simulation Engine。

最终系统必须能够作为 Graphene / Ignition Demo 环境的真实 Virtual Facility 数据源，并为后续 AI Engineer Agent 提供足够完整、可信、相关联的工程证据。

---

# 1. Primary Goal

不要构建：

> 一堆独立随机变化的 Tags。

必须构建：

> 一个具有资产关系、物理因果、设备状态、环境影响、电气拓扑、控制反馈、故障传播和时间连续性的 Data Centre Virtual World。

目标：

```text
Virtual Facility
    ↓
Physical relationships
    ↓
Equipment behaviour
    ↓
Sensors / Commands / Feedback
    ↓
Graphene-compatible data points
    ↓
OPC UA
    ↓
Ignition / Historian / Alarm
    ↓
AI Engineer Agent
```

AI Agent 将来应该能够回答：

```text
What happened?

What is the probable root cause?

What evidence supports it?

What has been ruled out?

What upstream/downstream systems are affected?

Is this mechanical, electrical, environmental,
network, data-quality or control-related?

What should an engineer check next?
```

而不能因为模拟世界缺乏必要 datapoints 而只能猜测。

---

# 2. Mandatory Reference Inputs

本任务会提供两个真实 Graphene Demo export：

```text
real-graphene-demo-udt-definitions.json

real-graphene-demo-tag-instances.json
```

它们来自当前实际已经搭建的 Graphene Demo Gateway。

这两个文件是：

> **Graphene Schema Authority**

必须在开始 architecture implementation 前完整解析。

建议保存到：

```text
reference/graphene/
├─ real-graphene-demo-udt-definitions.json
└─ real-graphene-demo-tag-instances.json
```

这些文件：

```text
READ ONLY
```

不要直接修改 source export。

---

# 3. Source-of-Truth Priority

当不同设计资料发生冲突时，优先级：

```text
1. Real Graphene UDT Definition Export
2. Real Graphene Tag Instance Export
3. Explicit requirements in this specification
4. Engineering diagnostic extensions
5. Implementation convenience
```

也就是说：

> 不允许为了让代码更整齐而改掉真实 Graphene 的名称和 hierarchy。

---

# 4. Graphene Compatibility Principle

对于 export 已经存在的内容，保留：

```text
folder name
instance name
typeId
member name
dataType
engineering unit
alarm semantics
history intent
parameters
hierarchy
```

不要保留：

```text
old opcServer
old opcItemPath
Device Connection
Modbus device address
Simulator Device
old runtime values
test values
now()-based simulation expressions
Gateway-specific connection information
```

---

# 5. Critical Path Correction

以前的设计曾使用：

```text
DC01/<Graphene system>/<asset>/<member>
```

作为 Tag path。

新的真实 export 已经成为 path authority。

因此：

> **Graphene provider-relative path 必须匹配真实 export。**

例如真实结构：

```text
CRAC/R_CRAC1/Supply Air Temperature

Meter/Level 1_DB_20/Ptot

UPS/UPS 8/Rectifier Failure

Cooling Towers Plant/R_P1_CT1/Power
```

不得为了内部 architecture 强制改成：

```text
DC01/Mechanical/CRAC/...
```

或：

```text
DC01/CRAC/...
```

如果 Domain Model 需要 Site ID：

```text
siteId = DC01
```

应作为：

```text
domain metadata
topology metadata
```

存在。

不要通过增加一个新的 Tag Folder 改变真实 Graphene browse path。

---

# 6. Export Path Is Immutable

每个 export Tag 都必须保存：

```text
exportPath
```

例如：

```text
CRAC/R_CRAC1/Supply Air Temperature
```

如果内部模型使用：

```text
assetKey = r_crac1
signalKey = supply_air_temperature
```

必须存在明确 mapping：

```text
internal signal
        ↓
Graphene mapping
        ↓
exact exportPath
```

不能靠字符串猜测。

---

# 7. Current Export Sanity Baseline

实现 bootstrap parser 时，应首先对当前输入做 inventory。

当前这批 reference export 的 parser sanity check 应大致得到：

## UDT Definition Export

```text
47 UdtType nodes
~1,857 AtomicTag definitions
```

## Tag Instance Export

```text
~296 folders
~831 UDT instances
~8,741 AtomicTags
```

这些数字不是长期硬编码 contract。

真正 authority 是输入文件。

但当前 parser 如果相差巨大：

> 说明 parser 有问题，不能继续 implementation。

---

# 8. Do Not Target an Old Fixed Point Count

禁止以：

```text
241 points
1565 points
38 assets
```

之类旧阶段数字作为 completion target。

新的规则：

> **覆盖真实 export，而不是追固定数字。**

最终 point count 应由：

```text
real instance export
+
schema normalization
+
approved diagnostic extensions
```

自动产生。

---

# 9. Mandatory Coverage Manifest

在开始 physical implementation 前必须生成：

```text
config/generated/graphene-schema.json

config/generated/graphene-instance-topology.json

config/generated/graphene-coverage-manifest.json

docs/graphene-schema-audit.md
```

Coverage Manifest 对每一个 exported AtomicTag 至少记录：

```text
exportPath

folderPath

instanceName

typeId

memberName

dataType

engUnit

historyIntent

alarmDefinitions

sourceClass

simulationOwner

domainSignal

origin

AI visibility
```

---

# 10. Source Classification

每个 exported AtomicTag 必须属于且只属于一个主要 category：

```text
PHYSICAL_SOURCE

PHYSICAL_DERIVED

CONTROL_COMMAND

CONTROL_FEEDBACK

EQUIPMENT_STATE

FAULT_STATE

ELECTRICAL_DERIVED

ENERGY_INTEGRAL

ENVIRONMENT

NETWORK_STATE

AGGREGATE

PRESENTATION_DERIVED

STATIC_METADATA

SUPPORT_CONTROL

TEST_SUPPORT
```

禁止：

```text
UNMAPPED
UNKNOWN_AND_IGNORED
```

留在最终 manifest。

---

# 11. 100% Accounting Rule

对于：

```text
real-graphene-demo-tag-instances.json
```

里面的所有 AtomicTag：

> 100% 必须进入 coverage manifest。

没有任何 point 可以被 silent drop。

---

# 12. Runtime Coverage Rule

所有被分类为：

```text
PHYSICAL_SOURCE
PHYSICAL_DERIVED
CONTROL_COMMAND
CONTROL_FEEDBACK
EQUIPMENT_STATE
FAULT_STATE
ELECTRICAL_DERIVED
ENERGY_INTEGRAL
ENVIRONMENT
NETWORK_STATE
AGGREGATE
PRESENTATION_DERIVED
```

的 points：

> 必须具有确定性的 runtime value source。

---

# 13. Support/Test Tags

真实 export 也包含类似：

```text
DemoRack
MQTT Tags
PredictionCache
Testing
```

这些仍然必须：

```text
appear in coverage manifest
```

但是可以标记：

```text
scope = SUPPORT
```

或：

```text
scope = TEST
```

这些 Tag：

* 不应驱动物理模型；
* 不应被 AI Engineer Agent 当成生产工程证据；
* 不应污染 Golden Demo root-cause analysis；
* 如果需要 Graphene drop-in compatibility，可以投影为 static/support values。

不能直接删除而没有记录。

---

# 14. AI Agent Production Surface

建立明确：

```text
aiVisible = true / false
```

规则。

正常：

```text
physical process
equipment
environment
electrical
network
alarm evidence
history evidence
```

可以：

```text
aiVisible = true
```

而：

```text
Testing
PredictionCache
MQTT quickstart/helper
developer control
scenario truth
runtime injection truth
admin internals
```

必须：

```text
aiVisible = false
```

AI Agent 不应获得幕后 scenario truth。

---

# 15. Actual Graphene Root Structure

当前真实 instance export 至少包含以下 root-level structures。

这些名称必须按 export 精确保留：

```text
AC Makeup Tank
BCPM
Breaker
Buffer Tank

Chiller
Chiller System Control
Chiller_System

Cold Water and Sanitary System
Cooling Towers Plant

CRAC

Dashboard
DemoRack
Diesel

Environment Monitoring
FCU
Fire Protection System
FWU

Genset
IPS

Level_Monitoring
Lift Monitoring System
Line

Meter

MQTT Tags

Network Switches
Network Topology

Other

PAHU

PredictionCache
Pressure System

RCMS

Smart Alarm Logic

Temperature and Humidity
Temperature_Controls

Testing
TIW

UPS

Water Leak Detection System
```

以及 root-level helper tags，例如：

```text
Device Card Abbreviation
```

不要自行：

```text
rename
merge
reparent
translate
```

这些结构。

---

# 16. Actual UDT Type Inventory

当前 UDT reference 至少包含以下类型。

名称必须保持 exact：

```text
Production/GDC230
Production/GPQM144 Pro
Production/GPM96
Production/GEM130
Production/GEM630
Production/E820
Production/GEM230
Production/GPQM96

AC Makeup Pump
BCPM
Breaker
Buffer Tank
CDU

Chiller
Chiller Pump
Chiller Valve
Cooling Tower
CRAC

CW Booster Pump
CW Ground Tank
CW Ground Valve
CW Roof Tank
CW Roof Valve
CW Transfer Pump

Dashboard
DI Event Test
Diesel
Environment Monitoring
FCU
FWU

GEM230
GEM630
GEM630-CT-L
Genset
GPM96
GPQM96
GPQM144

IPS
Makeup Water Pump

Network Device
Network Switch
Network Switch Port

PAHU
RCMS

Temperature and Humidity

UPS

Water Leak Cable Sensor
```

不能创建：

```text
UDT_CRAC
UDT_Chiller
UDT_UPS
UDT_Meter
```

这种平行 naming system。

---

# 17. Current Asset Population Must Be Represented

不要只创建几台“示范设备”。

当前实际 export 中的重要 population 包括：

```text
CRAC                15 instances
PAHU                15
FWU                 10
FCU                  5

Chiller              3
Chiller Pump         9
Chiller Valve       16

Buffer Tank          8

Cooling Tower       20
Makeup Water Pump   20

UPS                  25

Environment Monitoring
                    168

Temperature and Humidity
                     64

Network Device       20

Network Switch        4
Switch Ports        192

Water Leak Cable
Sensors              22

BCPM                 24

Meter                47

Genset                6

Diesel                3

IPS                   6
RCMS                  6

CDU                   3
```

以及实际 export 中的其他实例。

这些数字作为当前 reference sanity check。

不要硬编码。

由 parser 读取实例文件生成 topology。

---

# 18. Full World Scope

新的 Domain Model 必须覆盖比旧版本更完整的数据中心。

至少包括：

## Cooling / HVAC

```text
Chillers
Chiller Pumps
Chiller Valves
Cooling Towers
Makeup Water Pumps
Buffer Tanks

CRAC
PAHU
FWU
FCU

Cooling Blocks
Bypass Valves
Headers
Plant Sensors
```

## Water

```text
AC Makeup Tank
Cold Water Ground Tanks
Roof Tanks
Transfer Pumps
Booster Pumps
Ground Valves
Roof Valves
Pressure
Level
Flow
```

## Electrical

```text
Utility / Incomer Metering
MSB / DB Metering
47 Meter instances
BCPM
Breakers
UPS
IPS
RCMS
Gensets
Diesel/Fuel
```

## Environment

```text
distributed temperature
distributed humidity
outside weather
wet bulb
dew point
pressure
zone conditions
```

## Network

```text
network devices
switches
ports
dependency topology
```

## Safety / Facility

```text
Water Leak Detection
Fire Protection
Lift Monitoring
Level Monitoring
```

## Data-Centre / Liquid Cooling

```text
TIW / CDU
```

where present in actual export.

---

# 19. Chiller System Control Is a Real Graphene Surface

不要把：

```text
Chiller System Control
```

当成 disposable simulator logic。

真实 export 中该 structure 包含大量：

```text
plant control state
PIDs
setpoints
running chillers
required chillers
rotation
buffer tanks
cooling towers
pumps
weather
data hall temperature
flow
pressure
plant load
COP
staging
```

这些 paths 必须出现在 compatibility surface。

---

# 20. Chiller_System Is Also Part of Compatibility Surface

真实 export 中：

```text
Chiller_System
```

包含例如：

```text
Main Headers
Chillers
Header Motorized Valves
Bypass Valves
Ceiling Cooling Units
Cooling Blocks
```

这些不能被新的 cleaner architecture 随意删除。

但它们的 runtime value：

> 必须来自同一个 Domain Model。

禁止复制旧 `sin(now())` expression。

---

# 21. Domain vs Presentation

一个 physical signal 可以有多个 Graphene presentation points。

例如：

```text
Domain:
CH-001 chilled water supply temperature
```

可能投影到：

```text
Chiller System Control/Chillers/CH-001/CHWS Temp

Chiller_System/Chillers/.../Temperature
```

这是允许的。

但两个点：

> 必须引用同一个 authoritative Domain Signal。

不能分别计算。

---

# 22. Signal Registry

建立中央：

```text
SignalRegistry
```

概念。

例如：

```text
signalKey:
chiller.ch_001.chws_temperature
```

Mapping：

```text
Graphene Point A
Graphene Point B
3D Inspector
Admin API
```

都引用同一个 signal。

这样避免重复物理逻辑。

---

# 23. Domain Model Core

核心接口：

```python
snapshot = model.calculate(timestamp, world_profile)
```

或语义等价接口。

输入：

```text
timezone-aware timestamp
seed
world configuration
asset topology
world profile
scenario activations
```

输出：

```text
BaseWorldSnapshot
```

包含：

```text
simulation timestamp

asset states
commands
feedback
sensor values
zones
cooling plant
water systems
electrical
network
environment
energy
scenario-derived physical truth
```

---

# 24. Determinism

同一：

```text
timestamp
seed
config
world profile
```

必须返回完全相同的：

```text
BaseWorldSnapshot
```

禁止：

```text
datetime.now()
time.time()
random.random() without stable derivation
database reads
HTTP
OPC read
hidden mutable physical state
```

---

# 25. Deterministic Noise

使用：

```text
seed
+
stable signalKey
+
UTC time bucket
↓
SHA-256
↓
stable normalized noise
```

调用顺序不得改变结果。

---

# 26. Time

内部：

```text
UTC
```

默认显示：

```text
Asia/Singapore
```

Model epoch：

```text
2026-05-29T16:00:00Z
```

timestamp < epoch：

```text
reject
```

---

# 27. Real Physical Relationships

所有重要 datapoint 必须尽可能共享物理 source。

例如：

```text
pump speed
↓
flow
↓
differential pressure
↓
cooling capacity
```

以及：

```text
IT heat load
↓
zone cooling demand
↓
airside demand
↓
CHW demand
↓
chiller loading
↓
cooling tower demand
↓
electrical power
```

---

# 28. Diagnostic Observability Rule

对于任何重要 equipment，AI 排查时至少应该能够观察：

```text
command

feedback

run status

mode

process input

process output

loading

energy/power where relevant

fault/trip

communication/data quality

upstream relationship

downstream impact
```

不能只有：

```text
HasAlarm
```

一个点。

---

# 29. Existing Points First

如果 export 已有足够证据：

> 使用现有 point。

不要为了漂亮再复制一个 extension。

只有：

```text
existing export cannot provide required evidence
```

时才增加 Diagnostic Extension。

---

# 30. Extension Rules

新增 point：

```text
origin = twin-extension
```

并保存：

```text
extensionReason
```

例如：

```text
root-cause-evidence
physical-consistency
AI-diagnostic-observability
```

Extension：

* 不得覆盖 existing member；
* 不得偷偷改变 existing member semantics；
* 必须拥有 dataType；
* 必须拥有 engUnit where applicable；
* 必须拥有明确 signal owner。

---

# 31. Mandatory CRAC Diagnostic Evidence

真实 CRAC UDT 原有结构必须全部保留。

另外如果 export 不存在等价 evidence，至少补：

```text
CHW Valve Command
CHW Valve Feedback
CHW Flow
```

推荐进一步保证可观察：

```text
CHW Supply Temperature
CHW Return Temperature
Fan Electrical Power
```

如果这些 evidence 已经存在于相关 exported control path：

> 优先关联现有 signal，不必重复。

---

# 32. Mandatory Chiller Diagnostic Evidence

至少保证 AI 能取得：

```text
Load
Input Power
Cooling Output

COP
kW per RT

CHW Supply Temperature
CHW Return Temperature
CHW Flow

CW Supply Temperature
CW Return Temperature
CW Flow

CW Approach

Condenser Pressure

Operating Hours
On_Off
Auto_Manual
Fault/Trip
```

Existing Graphene points 优先。

缺失时添加 extension。

---

# 33. Chiller Pump Diagnostic Sufficiency

真实 `Chiller Pump` UDT 当前信息非常少。

为真实排查能力，至少确保逻辑设备存在：

```text
On_Off
Auto_Manual

Power
Current

Frequency

Speed Command
Speed Feedback

Flow

Suction Pressure
Discharge Pressure
Differential Pressure

System Failure_Trip
HasAlarm

Run Hours
```

只新增 export 缺失的部分。

---

# 34. Chiller Valve Diagnostic Sufficiency

至少：

```text
Command
Feedback
Position

On_Off
Auto_Manual

Fault
```

必须能够区分：

```text
commanded open
```

和：

```text
actually open
```

否则 AI 无法判断：

```text
actuator/valve failure
```

---

# 35. Cooling Tower Diagnostic Sufficiency

除 export 原有：

```text
Frequency
Power
Energy
Current
Voltage
PF
On_Off
Fault
```

外，要确保 AI 能取得：

```text
CWR Temperature
CWS Temperature
Approach

Fan Speed Command
Fan Speed Feedback

Basin Level
Run Hours
```

如果这些已经存在于：

```text
Chiller System Control
```

则通过 Signal Registry 关联。

不要复制独立计算。

---

# 36. PAHU Diagnostic Sufficiency

保留现有：

```text
SAT
RAT
RH
setpoints
On_Off
Fan On_Off
Auto_Manual
alarms
```

并确保有：

```text
Static Pressure
Static Pressure Setpoint

Fan Speed Command
Fan Speed Feedback

CHW Valve Command
CHW Valve Feedback
CHW Flow
```

适用于真实 airflow / valve root-cause analysis。

---

# 37. FWU / FCU Diagnostic Sufficiency

已有：

```text
SAT
RAT
RH
CHWS
CHWR
Flow
Static Pressure
Filter Alarm
Communication Alarm
```

等 point 时必须保留。

如果缺少 command/feedback，可补：

```text
Fan Speed Command
Fan Speed Feedback

CHW Valve Command
CHW Valve Feedback
```

---

# 38. Buffer Tank

真实 export 已经拥有较丰富：

```text
stratified temperatures
valve command/status
recharge flow
recharge control/feedback
mode
CHWS inlet/outlet
```

以及 control surface 中的：

```text
level
pressure
capacity
average temperature
flow
```

这些必须关联成同一个 Buffer Tank world。

---

# 39. UPS Diagnostic Sufficiency

真实 UPS UDT 必须完整支持。

另外至少保证：

```text
Input Voltage
Output Voltage
Frequency

Input Power
Output Power
Load %

Rectifier Failure

Inverter Status
Bypass Status

Battery SOC
Battery Voltage
Battery Current
Battery Runtime Remaining

Battery Charging Failure

UPS Common Alarm
```

如果现有 export 的某些 unit 与名称存在明显冲突：

例如：

```text
Input Power
```

却标：

```text
V
```

不要直接猜测。

进入：

```text
metadata-normalization review
```

直到有可靠依据。

---

# 40. Genset / Diesel Diagnostic Sufficiency

现有 Genset：

```text
run state
frequency
voltage
battery
engine speed
coolant temperature
oil pressure
faults
```

必须保留。

建议补足：

```text
Active Power
Reactive Power
Apparent Power
Load %
Current
Energy
Breaker Status
Fuel Level
```

Diesel/Fuel system：

至少应有：

```text
tank level
pump status
flow
totalized flow
inlet valve feedback
fault
```

用于：

```text
mains failure
→ genset start
→ fuel availability
```

的完整诊断链。

---

# 41. CDU / TIW Diagnostic Sufficiency

当前真实 export 已有 CDU/TIW 实例。

不要从新世界中删除。

为了支持液冷/IT water diagnosis，建议确保：

```text
Supply Coolant Temperature
Return Coolant Temperature

Flow
Differential Pressure

Pump Run
Pump Speed
Pump Power

IT Load
Facility Load
Heat Load

Unit Running Status
Alarm Status
```

只有 export 缺失的才作为 extension。

---

# 42. Environment

必须保留完整：

```text
Environment Monitoring
```

以及：

```text
Temperature and Humidity
```

真实 instance population。

每个 environmental sensor 必须有：

```text
zone
location
```

metadata。

这样 AI 可以：

```text
identify local vs site-wide condition
```

而不是只看到一百多个没有空间关系的温湿度点。

---

# 43. Environment Spatial Mapping

建立：

```text
zoneId
roomId
rowId
optional rack/area
```

metadata。

例如：

```text
Hall-A
Hall-B
Level 1
Level 2
```

必须能够将：

```text
CRAC
FWU
temperature sensors
leak cable
rack load
```

关联到相同空间区域。

---

# 44. Network

必须同时支持：

```text
Network Topology
Network Switches
```

不是只保留简单 `Network Device`。

---

# 45. Network Device Signals

至少保持：

```text
Comm
Status
Ping Time

CPU
Memory
Temperature
Uptime
```

---

# 46. Network Switch Port Model

真实 export 已具有大量 port-level datapoints。

至少：

```text
Admin Status
Link Status
Display Status

Speed

In Utilization
Out Utilization

Error Count

PoE Power

Description
```

必须由 topology 驱动。

---

# 47. Network Causality

例如：

```text
SW-03 network failure
↓
switch state
↓
affected ports
↓
downstream device communication
↓
Comm / Status / Ping
```

禁止：

```text
24 random devices independently fail
```

---

# 48. Water Leak

保持：

```text
Leak Position
Status
Cable Length
```

以及 alarm semantics。

Leak sensor 必须具有：

```text
zone
physical area
cable identity
```

metadata。

Leak event 应同时影响：

```text
sensor
location
local humidity where physically reasonable
```

---

# 49. Fire Protection

`Fire Protection System` 是真实 Graphene tree 的一部分。

必须进入：

```text
coverage manifest
runtime compatibility surface
```

其状态不能被随机生成。

保持：

```text
zone dependency
normal/fire/trouble state
```

如果本项目没有专门 fire scenario：

> 保持 deterministic healthy baseline。

不要为了有动画制造假火灾。

---

# 50. Cold Water / Sanitary

必须建模实际：

```text
Ground Tank
Roof Tank

Transfer Pump
Booster Pump

Ground Valve
Roof Valve
```

基本因果：

```text
tank level
↓
pump demand
↓
pump status
↓
flow / pressure
↓
tank recovery
```

不得各点随机。

---

# 51. Electrical World

原 47-meter 设计继续保留。

但它不再代表全部 electrical world。

必须同时考虑：

```text
Meter
BCPM
Breaker
UPS
IPS
RCMS
Genset
```

以及相关 load relationships。

---

# 52. Actual Meter Surface

真实 `Meter` folder 当前包含 47 个 UDT instances。

类型包括：

```text
GPM96
GPQM144
GPQM96
GEM230
GEM630
GEM630-CT-L
```

保持真实 instance name 和 typeId。

不要重新生成：

```text
METER-001
METER-002
```

替代现有名称。

---

# 53. Meter Members

必须支持当前 export 的：

```text
P1
P2
P3
Ptot

Q1
Q2
Q3
Qtot

S1
S2
S3
Stot

PF1
PF2
PF3
PFsys

I1
I2
I3
In
Isys

V1
V2
V3
V12
V23
V31
Vsys

Hz

THDV1
THDV2
THDV3

THDA1
THDA2
THDA3

Wh_Im

HasAlarm
```

根据实际 meter type subset。

---

# 54. Meter Units

对于 metadata 不完整的 meter UDT：

建立 normalization table。

标准 engineering interpretation：

```text
V*        V
I*        A

P*        kW
Q*        kVAR
S*        kVA

PF*       dimensionless

Hz        Hz

THD*      %

Wh_Im     kWh
```

只有语义明确时才 normalize。

原始 export metadata 必须保存在：

```text
sourceMetadata
```

中供审计。

---

# 55. Meter Electrical Consistency

共享：

```text
P
PF
```

派生：

```text
S
Q
Current
```

禁止每个 quantity 独立 random。

---

# 56. Meter Hierarchy

建立明确 parent/child graph：

```text
upstream incomer
↓
MSB
↓
DB
↓
branch
↓
load
```

每个 meter metadata 至少：

```text
meterId
parentMeterId
level
location
loadCategory
assetLinks
lossFraction
```

---

# 57. Reconciliation

对于 aggregate：

```text
parent power
=
direct child power / (1 - modeledLoss)
```

目标：

```text
reconciliation error < 2%
```

Virtual aggregate 禁止独立 random。

---

# 58. BCPM

真实 export 有完整 BCPM instances。

必须支持：

```text
Current
Active Power
Accumulated Energy
Rack ID
```

BCPM 应关联：

```text
rack / IT load
```

从而：

```text
IT rack load
↓
BCPM
↓
upstream meter
```

能够 reconciliation。

---

# 59. Breakers

保持真实：

```text
OnOff
Trip
EF
```

breaker 应存在：

```text
upstream/downstream relationship
```

如果 breaker trip：

```text
downstream electrical load
```

必须合理受影响。

---

# 60. Energy Integration

所有累计 energy：

```text
Wh_Im
Accumulated Energy
```

必须来源于：

```text
power integration
```

而不是 random walk。

---

# 61. Energy Determinism

从：

```text
model epoch
```

开始积分。

使用确定性 fixed interval / trapezoidal integration。

Random access：

```text
calculate(T10)
calculate(T2)
calculate(T50)
calculate(T2)
```

两个 T2 必须相同。

---

# 62. Long-Running Open World Energy

因为 Open World 可以运行数月：

禁止每 tick 从 epoch 全量重新积分。

实现：

```text
deterministic chunk checkpoints
+
cache
```

例如：

```text
day boundary checkpoint
```

但：

```text
cache ≠ truth
```

清 cache 后结果完全相同。

---

# 63. Dashboard

真实 Dashboard points：

```text
PUE
WUE
Plant Efficiency
Total Facility Load
Total IT Load
UPS Load Factor
Heat Rejection
Central Cooling
etc.
```

这些是：

```text
PRESENTATION_DERIVED
```

必须从底层 domain quantities 计算。

禁止独立随机。

---

# 64. Smart Alarm Logic

真实：

```text
Smart Alarm Logic
```

进入 compatibility surface。

但其输出必须：

```text
derived from underlying equipment/fault state
```

不能成为 fault truth authority。

---

# 65. Runtime Quality

必须区分：

```text
process abnormal
```

和：

```text
data quality abnormal
```

例如 mechanical valve stuck：

```text
Value abnormal
Quality Good
```

如果 sensor/communication 正常。

Network loss：

```text
Good
→ Uncertain / Bad / stale
```

才合理。

---

# 66. Data Type Normalization

真实 export 某些 memory tags 没有 explicit dataType。

禁止依赖：

```text
Ignition implicit default
```

生成新 simulator schema。

建立：

```text
normalization-review.json
```

每个修正：

```text
exportPath
sourceValue
normalizedValue
confidence
reason
```

---

# 67. Engineering Unit Normalization

明显 metadata 缺失可补：

例如：

```text
Hz
```

缺 unit 时可安全补：

```text
Hz
```

但有歧义：

```text
UPS/Input Power L1 = V
```

必须进入 review。

不要为了 test pass 猜。

---

# 68. World Topology Is Mandatory

仅有 Tags 不够 AI 判断因果。

建立：

```text
WorldTopology
```

关系至少包括：

```text
contains

locatedIn

serves

servedBy

feeds

fedBy

upstreamOf

downstreamOf

meteredBy

connectedTo

networkParent

redundantWith
```

---

# 69. Topology Example

例如：

```text
CH-001
↓
CHW Header
↓
CRAC group
↓
Hall-A
```

Electrical：

```text
Utility
↓
MSB
↓
UPS
↓
DB
↓
BCPM
↓
Rack
```

Network：

```text
Core Switch
↓
Distribution Switch
↓
Port
↓
Device
```

AI Agent 后续才能自动追 dependency。

---

# 70. World Modes

必须有：

```text
demo
open_world
```

共享：

```text
same physical models
same assets
same Graphene projection
same topology
```

只是 orchestration profile 不同。

---

# 71. Demo Mode

Demo Mode：

```text
fixed deterministic exhibition timeline
```

Demo Day：

```text
2026-08-28 Asia/Singapore
```

UTC：

```text
2026-08-27T16:00:00Z
→
2026-08-28T16:00:00Z
```

默认：

```text
60x
loop = false
```

到结束：

```text
HOLDING
```

---

# 72. Demo Scenarios

至少：

```text
CH004_CONDENSER_DEGRADATION

CRAC01_VALVE_STUCK

SW03_NETWORK_FAILURE

UPSB_RECTIFIER_FAULT

HALLB_ROWC_LEAK

PAHU02_AFTER_HOURS
```

---

# 73. Demo Scenario Timing

Singapore local：

```text
CH004_CONDENSER_DEGRADATION
2026-07-29 08:00 onward
30-day ramp then hold

CRAC01_VALVE_STUCK
09:00–12:00
5-minute ramp

SW03_NETWORK_FAILURE
10:00–11:00

UPSB_RECTIFIER_FAULT
10:10–11:20

HALLB_ROWC_LEAK
10:20–12:00

PAHU02_AFTER_HOURS
19:00–23:00
```

---

# 74. Scenario Engine

Scenario Engine 只生成：

```text
FaultActivation
```

包括：

```text
faultType
target
severity
parameters
```

不直接写最终 Tag。

---

# 75. Shared Fault Engine

Demo 与 Open World 必须共享 fault implementation：

```text
Demo Scenario Engine
        │
        ▼
FaultActivation
        ▲
        │
Runtime Fault Controller
        │
        ▼
Shared Fault Effect Engine
        ↓
Physical consequences
```

不能有：

```text
demo CRAC fault implementation
```

和：

```text
open-world CRAC fault implementation
```

两套。

---

# 76. CRAC Valve Stuck Target

Full severity：

```text
CHW Valve Command ≈ 82%
CHW Valve Feedback ≈ 3%
CHW Flow ≈ 0.3 L/s

Supply Air Temperature ≈ 22.4 °C

Filter Choke Alarm = false

On_Off = 1
```

Hall-A temperature：

```text
~1.1 °C rise
```

---

# 77. CH-004 Degradation Target

Full degradation：

```text
CW Flow ↓

CW Approach
3.2 K → ~5.5 K

Condenser Pressure ↑

Cooling Tower demand ↑

Cooling Tower Power ↑

CH-004 Input Power ↑

COP
~4.44 → ~3.89
```

保持：

```text
Cooling Output = Input Power × COP

kW/RT = 3.517 / COP
```

CH-004 design capacity：

```text
1450 kW
```

peak load：

```text
~65–75%
```

---

# 78. UPS-B Fault

同一 fault：

```text
Rectifier Failure
↓
Battery Charging Failure
↓
UPS Common Alarm
```

peer UPS unaffected。

---

# 79. Network Failure

同一个 SW root event：

```text
switch
↓
ports
↓
downstream devices
```

最终改变：

```text
Comm
Status
Ping Time
```

以及 affected port values。

---

# 80. Hall-B Leak

同一 leak：

```text
Leak status
Leak position
Hall-B local humidity
```

其他无关 zone 不受影响。

---

# 81. Commissioning Deviations

至少保留：

```text
CHWS actual setpoint
5.3 °C

design
6.5 °C ±0.2
```

PAHU：

```text
actual static pressure SP
600 Pa

design
450 Pa
```

以及：

```text
PAHU-02 after-hours operation
19:00–23:00
```

---

# 82. Cooling Plant Load Profile

基础：

```text
00:00   620 kW
06:00   690 kW
09:00   820 kW
13:00  1030 kW
15:00  1080 kW
19:00   910 kW
23:00   670 kW
```

linear interpolation。

叠加：

```text
slow trend
deterministic correlated noise
physical fault effects
```

---

# 83. Open World Mode

Open World：

> indefinitely running Virtual Facility

不是：

> Demo Day 去掉 end clamp。

---

# 84. Open World Baseline

Open World 默认：

```text
no Demo scripted fault windows
```

而是：

```text
healthy deterministic operating baseline
+
time-of-day profile
+
day-of-week profile
+
weather/environment variation
+
slow deterministic trend
+
operator-injected faults
```

---

# 85. Infinite Timeline

Open World：

```text
worldStartUtc
↓
hours
days
weeks
months
```

不断运行。

不能进入：

```text
HOLDING
```

---

# 86. Runtime Fault Injection

支持：

```text
inject
update severity
clear
clear_all
list active
```

Fault instance：

```text
injectionId

recipeId

targetAsset

severity

simulationTimestamp

optional ramp

optional duration

parameters
```

---

# 87. Runtime Fault Catalog

至少：

```text
CRAC_VALVE_STUCK

NETWORK_DEVICE_FAILURE

UPS_RECTIFIER_FAULT

WATER_LEAK

CHILLER_CONDENSER_DEGRADATION
```

但 catalog 必须：

```text
data driven
```

而不是 frontend hardcode。

---

# 88. Runtime Fault Overlay

正确：

```text
BaseWorldSnapshot
↓
Runtime Fault Effect
↓
EffectiveWorldSnapshot
```

Runtime fault：

> 不得改变 deterministic BaseWorldSnapshot。

---

# 89. Raw Point Override

Raw override 位于：

```text
Effective World
↓
Graphene Projection
↓
Raw Override
↓
Published Point
```

它是：

```text
DEVELOPER-ONLY
```

允许故意破坏 physical consistency 进行 debug。

必须明确标记。

---

# 90. Reset / Seek Semantics

Reset：

```text
only resets simulation clock
```

默认：

```text
does NOT clear runtime faults

does NOT clear raw overrides
```

Clear fault/override 必须使用独立 command。

---

# 91. Runtime Lifecycle

Service process 与 simulation runtime 分离。

Simulation runtime states：

```text
STOPPED
STARTING
RUNNING
PAUSED
HOLDING
STOPPING
```

Open World 不进入 HOLDING。

---

# 92. Runtime Commands

统一支持：

```text
start
stop

pause
resume

reset
seek

set-time-scale
```

---

# 93. Command Service

CLI 和 Web 必须调用同一个：

```text
AdminCommandService
```

统一：

```text
validation
authorization
commandId
confirm
idempotency
audit event
error handling
```

---

# 94. OPC UA

使用：

```text
asyncua
```

Production namespace：

```text
urn:eetarp:graphene:demo:twin
```

---

# 95. OPC UA Browse Structure

Production OPC hierarchy：

> mirror Graphene export hierarchy。

不要重新设计一套：

```text
Mechanical
Electrical
Sensors
Commands
```

hierarchy。

---

# 96. OPC NodeId

使用：

```text
point:<exportPath>
```

例如：

```text
point:CRAC/R_CRAC1/Supply Air Temperature
```

字符串必须 stable。

---

# 97. OPC SourceTimestamp

必须：

```text
simulation UTC timestamp
```

而不是 server publish wall time。

---

# 98. OPC Surface Is Read-Only

禁止通过 production OPC：

```text
inject fault
write point
reset
seek
change speed
change mode
```

所有 mutation 走 Admin API。

---

# 99. Graphene Point Contract

每个 projected point：

```text
exportPath

value

dataType

engUnit

quality

sourceTimestamp

instanceName

typeId

memberName

signalKey

role

origin

scope

AI visibility
```

---

# 100. Point Origins

至少：

```text
graphene-export

graphene-normalized

twin-extension
```

不能把 extension 冒充真实 export。

---

# 101. Alarm / History Metadata

从 UDT export 继承：

```text
alarm name
mode
priority
setpoints
historyEnabled
sample intent
```

但：

> Simulator 不需要重新成为 Ignition Alarm Journal。

它提供：

```text
process truth
fault truth
metadata
```

未来 Ignition 执行 alarm evaluation。

---

# 102. Admin Backend

推荐：

```text
FastAPI / Starlette
```

提供：

```text
REST
SSE
OpenAPI
static frontend serving
```

保持一个 deployable service。

不要拆微服务。

---

# 103. Admin REST

至少：

```text
status

config

topology

schema coverage

assets

current snapshot

fault catalog

active faults

active overrides

health

logs

runtime events
```

Mutation：

```text
start
stop

pause
resume
reset
seek
set-time-scale

inject fault
clear fault
clear all faults

apply override
clear override
clear all overrides
```

---

# 104. Realtime

优先：

```text
REST + SSE
```

Initial：

```text
REST authoritative snapshot
↓
SSE connect
```

Reconnect：

```text
SSE reconnect
↓
REST resync
```

不能依赖遗漏 event。

---

# 105. Runtime Events

结构化：

```text
RUNTIME_STARTED
RUNTIME_STOPPED
RUNTIME_PAUSED
RUNTIME_RESUMED

MODE_CHANGED

FAULT_INJECTED
FAULT_CLEARED

OVERRIDE_APPLIED
OVERRIDE_CLEARED

CLOCK_SEEK
CLOCK_RESET
```

不要让 frontend parse log string 判断事件。

---

# 106. Logging

Backend：

```text
Python logging
+
bounded LogHub
```

包含：

```text
timestamp
level
source
message
structured context
```

绝不能 log secret。

---

# 107. React Admin App

Frontend：

```text
React
TypeScript
Vite
Tailwind
shadcn/ui
```

3D：

```text
Three.js
React Three Fiber
Drei
```

---

# 108. UI Design Goal

UI 应像：

> professional industrial Digital Twin Operations Console

而不是：

```text
developer HTML form

gaming HUD

neon dashboard
```

---

# 109. Pages

至少：

```text
Overview

Virtual World

Runtime

Faults

Assets

Terminal

System
```

---

# 110. Header

全局显示：

```text
Backend connection

World Mode

Runtime state

Simulation timestamp

Time scale

Active scripted scenarios

Active injected faults
```

---

# 111. Overview

5 秒内回答：

```text
Which mode?

Is it running?

What time?

What is abnormal?

Which systems affected?

Site power?

Cooling status?

Hall conditions?
```

---

# 112. Runtime UI

支持：

```text
Mode

Start
Stop

Pause
Resume

Reset
Seek

Time Scale

World Start Time
```

Demo 和 Open World UI 必须不同。

---

# 113. Fault UI

页面：

```text
Active Faults

Fault Catalog

Raw Overrides

Runtime Events
```

Fault source：

```text
SCRIPTED
INJECTED
RAW_OVERRIDE
```

统一显示。

---

# 114. Interaction Feedback

每个操作必须有：

```text
hover
pressed
confirming
pending
success
error
```

不能点击后没有反馈。

---

# 115. Base vs Effective vs Published

Inspector 在 fault/override 时支持：

```text
Base Value

Effective Value

Published Value

Value Source
```

例如：

```text
Valve Feedback

Base       78%
Effective   3%
Source      INJECTED
```

Raw override：

```text
SAT

Base       22.4 °C
Effective  22.4 °C
Published  35.0 °C

Source
RAW_OVERRIDE
```

Frontend 不自己计算。

---

# 116. Assets UI

支持：

```text
search

system

asset type

zone

status

quality

fault source
```

并显示：

```text
asset
type
location
state
key metrics
fault
quality
last update
```

---

# 117. 3D Virtual World

必须 data driven。

Topology：

```text
WorldTopology
↓
Scene Builder
```

不能在 React components 里散落：

```text
CRAC-01 position
CH-004 position
```

---

# 118. 3D Areas

至少表示：

```text
Data Halls

Cooling Plant

Electrical

Network

Water / Utility

Facility areas
```

根据真实 Graphene topology。

---

# 119. 3D Major Assets

至少表示：

```text
Chillers

Pumps

Valves

Cooling Towers

Buffer Tanks

CRAC

PAHU

FWU

FCU

UPS

Gensets

Meters / Electrical groups

Network Switches

Leak Detection

Environmental Sensors

CDU
```

不需要 BIM 级模型。

---

# 120. 3D Interaction

支持：

```text
orbit
pan
zoom

reset camera

zone presets

focus asset

hover tooltip

click select

persistent Inspector
```

---

# 121. 3D Layers

至少：

```text
Equipment

Temperature

Flow

Pressure

Power

Faults

Environment

Network

Leak
```

不要默认显示八千多个 point marker。

---

# 122. 3D Source of Truth

3D 与：

```text
Overview
Assets
Inspector
```

必须读取同一个 frontend normalized runtime state。

不能有第二套数据。

---

# 123. Terminal

Developer Terminal：

```text
live logs

runtime events

search

filter

pause rendering

resume

auto-scroll

copy
```

必须使用：

```text
virtualization/windowing
```

避免长时间运行后 DOM 爆炸。

---

# 124. Security

Admin 默认：

```text
loopback
```

不要默认暴露 LAN。

禁止：

```text
VITE_ADMIN_TOKEN
```

把 secret compile 到 browser。

---

# 125. Admin Authentication

可采用：

```text
credential
↓
backend validation
↓
session
↓
HttpOnly cookie
```

token 不长期存 localStorage。

---

# 126. AI Agent Boundary

未来 AI Agent：

```text
READ ONLY
```

可以：

```text
browse/read operational Graphene points

alarms

history

network/device status

topology/knowledge
```

禁止：

```text
scenario truth

runtime fault controller

raw override state

admin controls

write
acknowledge
config mutation
```

Agent 必须通过工程证据自己推断 root cause。

---

# 127. AI Diagnostic Evidence Test

对于每个 Golden Demo root cause：

建立一个自动化：

```text
Diagnostic Evidence Sufficiency Test
```

验证 Agent 可见 surface 至少包含：

```text
root-cause evidence

supporting symptoms

peer comparison evidence

upstream evidence

downstream evidence

data quality evidence
```

不能只测试“Tag 存在”。

---

# 128. Example: CRAC Evidence Test

应存在：

```text
On_Off

Auto_Manual

SAT
RAT

Valve Command
Valve Feedback

CHW Flow

related Hall temperature

fault/trip

filter alarm

signal quality
```

这样才能排除：

```text
filter issue
sensor issue
unit off
```

并支持 valve root cause。

---

# 129. Example: Chiller Evidence Test

AI 至少可取得：

```text
load
power

CHW temperatures
CHW flow

CW temperatures
CW flow

condenser pressure

tower demand

COP
kW/RT

peer chiller data
```

---

# 130. Example: Electrical Evidence Test

AI 至少可取得：

```text
upstream meter

breaker state

voltage

current

P/Q/S

PF

frequency

THD

energy

UPS state

downstream load
```

---

# 131. Example: Network Evidence Test

至少：

```text
switch status

port status

port errors

utilization

ping

downstream device comm

topology parent
```

---

# 132. Coverage Verification Test

建立 CI test：

```text
parse real export
↓
load coverage manifest
↓
compare
```

必须满足：

```text
exported AtomicTags - manifest entries = 0
```

即：

```text
0 unmapped exported points
```

---

# 133. Runtime Coverage Test

对于所有 production/runtime points：

必须能够：

```text
project(snapshot)
```

得到：

```text
valid typed value
or explicit modeled unavailable quality
```

不能出现：

```text
missing key
silent None
KeyError
```

---

# 134. Schema Regression

建立 snapshot test：

```text
exportPath
typeId
memberName
dataType
engUnit
```

避免后续重构误改真实 Graphene contract。

---

# 135. Extension Regression

Extension 必须测试：

```text
does not shadow export point
```

即：

```text
extension exportPath
```

不得与 existing export 冲突。

---

# 136. Domain Determinism Tests

测试随机调用顺序：

```text
T3
T1
T20
T2
T1
```

两个 T1：

```text
identical
```

---

# 137. Physical Invariant Tests

至少：

```text
meter reconciliation < 2%

P/Q/S/PF consistency

energy continuity

cooling output = power × COP

flow/temperature/load causal consistency
```

---

# 138. Fault Causality Tests

必须覆盖：

```text
CRAC valve fault

CH-004 degradation

UPS fault

network failure

water leak
```

验证多个 correlated symptoms。

---

# 139. Demo Runtime Tests

验证：

```text
start
pause
resume
seek
reset
time scale
end hold
```

---

# 140. Open World Tests

验证：

```text
multi-day runtime

past old Demo boundary

no HOLDING

fault inject

fault recovery

long-running deterministic values
```

---

# 141. OPC UA Tests

实际启动 server 并使用真实 client：

```text
connect

browse

read

SourceTimestamp

stable NodeId

read-only rejection
```

---

# 142. REST/SSE Tests

覆盖：

```text
API schema

runtime commands

fault commands

auth

commandId

confirm

SSE telemetry

disconnect

reconnect

resync
```

---

# 143. Frontend Tests

至少：

```text
Vitest
React Testing Library
```

测试：

```text
runtime controls

fault cards

Inspector

connection status

stale handling

terminal

formatters

realtime store
```

---

# 144. 3D Tests

验证：

```text
topology mapping

asset selection

hover

camera

layers

telemetry updates

fault update

recovery
```

---

# 145. E2E

使用：

```text
Playwright
```

至少完成：

```text
Demo journey

Open World journey

Fault injection + recovery

Raw override

Backend disconnect/reconnect

Developer terminal
```

---

# 146. Visual QA

由独立 Subagent 验证：

```text
Overview

3D

Runtime

Faults

Assets

Terminal

System

Dark

Light

desktop

laptop

tablet landscape
```

---

# 147. Performance

验证：

```text
full export point surface

large environmental sensor set

network switch ports

meter surface

SSE telemetry

3D rendering

long logs

multi-day Open World

far-future energy
```

不允许：

```text
full scene rebuild each tick

unbounded log memory

one HTTP poll per component

energy O(total history) every tick
```

---

# 148. Recommended Repository

```text
/
├─ pyproject.toml
├─ uv.lock
├─ README.md
│
├─ reference/
│  └─ graphene/
│     ├─ real-graphene-demo-udt-definitions.json
│     └─ real-graphene-demo-tag-instances.json
│
├─ config/
│  ├─ world/
│  ├─ physics/
│  ├─ topology/
│  ├─ scenarios/
│  └─ generated/
│
├─ src/
│  └─ graphene_demo_twin/
│     ├─ schema/
│     ├─ domain/
│     ├─ physics/
│     ├─ electrical/
│     ├─ scenarios/
│     ├─ faults/
│     ├─ projection/
│     ├─ runtime/
│     ├─ adapters/
│     ├─ admin/
│     └─ cli/
│
├─ apps/
│  └─ web/
│
├─ tests/
│
├─ e2e/
│
└─ docs/
```

---

# 149. Main Agent Execution Model

强制采用：

```text
Main Agent
=
Architect
Planner
Orchestrator
Reviewer
Decision Maker
```

Subagents：

```text
inspect
implement
edit
run
test
debug
verify
```

Main Agent 不作为主要 coder。

---

# 150. First Mandatory Subagent

第一步必须派：

```text
Graphene Schema Audit Agent
```

它的任务：

```text
parse both real exports

inventory every folder

inventory every UDT type

inventory every UDT instance

inventory every AtomicTag

extract alarm/history/unit metadata

identify metadata anomalies

identify duplicated signals

identify old simulator expressions

identify AI diagnostic gaps

generate coverage manifest proposal
```

在这个任务 VERIFIED 前：

> 不允许开始大规模 physical model coding。

---

# 151. Second Mandatory Subagent

派：

```text
World Topology / Diagnostic Gap Agent
```

负责将实际 Graphene instances 转成：

```text
physical assets
systems
zones
upstream/downstream relationships
```

并找出：

```text
missing command
missing feedback
missing flow
missing pressure
missing power
missing topology
```

等 AI diagnostic blockers。

---

# 152. Third Architecture Gate

Main Agent review 前两份 summary 后，确定：

```text
Graphene contract

World topology

Diagnostic extensions

Physical model module boundaries

SignalRegistry
```

然后才能派发 implementation。

---

# 153. Recommended Agent Graph

```text
                    Main Agent
                        │
          ┌─────────────┴─────────────┐
          ▼                           ▼
 Graphene Schema Audit        Topology / Gap Audit
          │                           │
          └─────────────┬─────────────┘
                        ▼
                Architecture Gate
                        │
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
 Domain Foundation   Electrical      Cooling/Water
        │               │                │
        ├───────────────┼────────────────┤
        ▼               ▼                ▼
 Environment        Network          Facility/Safety
        └───────────────┼────────────────┘
                        ▼
                   Base World
                        │
             ┌──────────┼───────────┐
             ▼          ▼           ▼
        Scenarios   Open World   Fault Engine
             └──────────┼───────────┘
                        ▼
                     Runtime
                        │
             ┌──────────┼───────────┐
             ▼          ▼           ▼
          Graphene    OPC UA     Admin API
          Projection
                        │
                        ▼
                  React Admin
                        │
             ┌──────────┼──────────┐
             ▼          ▼          ▼
           3D UI      Runtime    Terminal
                        │
                        ▼
                    Integration
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
    Backend QA     Frontend QA     Visual QA
         └──────────────┼──────────────┘
                        ▼
                   Final Verify
```

---

# 154. Task Contract

每个 Subagent 必须收到：

```text
Objective

Scope

Owned files

Input contract

Architecture constraints

Dependencies

Required tests

Forbidden changes

Expected summary
```

禁止：

```text
"Build the simulator."
```

这种模糊任务。

---

# 155. Subagent Summary

格式：

```text
## Task

## Changes

## Files

## Contracts

## Verification

## Result
PASS / PARTIAL / FAIL

## Runtime Evidence

## Issues

## Dependencies

## Recommended Next
```

---

# 156. Main Agent Verification Rule

Main Agent 不接受：

```text
done
works
all implemented
```

作为证据。

必须有：

```text
actual files
commands
tests
results
runtime evidence
```

---

# 157. Independent Verification

以下必须由和 implementation 不同的 Subagent verify：

```text
Graphene export coverage

determinism

physical causality

electrical reconciliation

energy

Demo Mode

Open World

runtime faults

OPC UA

Admin API

SSE

3D data mapping

security

visual quality
```

---

# 158. Project State

使用：

```text
PLANNED

IN_PROGRESS

BLOCKED

IMPLEMENTED

VERIFYING

VERIFIED
```

始终：

```text
IMPLEMENTED != VERIFIED
```

---

# 159. Documentation

必须建立：

```text
docs/architecture.md

docs/graphene-schema-audit.md

docs/graphene-coverage.md

docs/topology.md

docs/diagnostic-extensions.md

docs/contracts.md

docs/implementation-plan.md

docs/decisions.md

docs/state.md
```

---

# 160. Definition of Done — Graphene Fidelity

必须满足：

```text
100% exported AtomicTags accounted

100% UDT instances inventoried

100% UDT types inventoried

0 silently dropped paths

0 renamed existing members

0 renamed existing typeId

0 hidden hierarchy rewrite
```

---

# 161. Definition of Done — Runtime Coverage

所有 operational exported point：

```text
has deterministic runtime source
```

或：

```text
has explicit modeled unavailable quality/state
```

不存在：

```text
forgotten point
```

---

# 162. Definition of Done — AI Diagnostic Sufficiency

对于核心设备：

```text
command
feedback
process values
state
fault
quality
upstream
downstream
peer evidence
```

足够让 AI 进行工程 root-cause analysis。

不能因为关键 evidence 不存在而只能输出：

```text
insufficient information
```

作为正常 Golden Demo 结果。

---

# 163. Definition of Done — No Fake Completeness

但是：

> 不允许为了“让 AI 有答案”而生成物理上不合理的数据。

原则：

```text
missing evidence
→ add justified diagnostic point

NOT

missing evidence
→ hardcode desired AI conclusion
```

---

# 164. Explicitly Out of Scope

这个 Repo 不实现：

```text
Ignition Gateway Project

Ignition UDT import deployment

Historian database

Alarm Journal database

MCP server

AI Agent implementation

Knowledge Base

production Modbus integration
```

但它必须为这些系统提供：

```text
complete coherent source world
```

---

# 165. Final Governing Rules

始终遵守：

> **The real Graphene exports define the compatibility surface.**

> **Every exported point must be accounted for.**

> **Existing Graphene paths, typeIds and member names are not redesigned for code cleanliness.**

> **Diagnostic extensions are additive, explicit and justified.**

> **Build one coherent world, not thousands of randomized tags.**

> **One physical signal may feed multiple Graphene presentation points, but it has only one authoritative calculation.**

> **Graphene derived/control surfaces must reuse the same Domain signals rather than copying old now()-based expressions.**

> **The Base World remains deterministic.**

> **Demo scripted scenarios and Open World runtime faults use the same physical fault mechanisms.**

> **Runtime fault injection does not corrupt deterministic Base World truth.**

> **Raw overrides are developer diagnostics, not physics.**

> **OPC UA exposes the world; it does not calculate the world.**

> **The React UI and 3D world visualize authoritative runtime data; they never invent telemetry.**

> **The AI Agent eventually sees engineering evidence, not scenario truth.**

> **Main Agent plans, delegates, reviews and decides. Subagents execute and verify.**

> **No component is VERIFIED without evidence.**

最终交付目标：

> 构建一个从真实 Graphene Demo Schema 出发，而不是从抽象示例出发的数据中心 Virtual World Engine；完整覆盖当前 Graphene 已存在的数据面，在此之上只增加对真实工程因果和 AI root-cause analysis 必需的 datapoints，使整个模拟环境在 Cooling、Electrical、Environment、Network、Water、Safety 和 Facility 层面形成一个统一、可重放、可长期运行、可注入故障、可通过 OPC UA 消费、并可由 AI Engineer 正确调查的完整数字孪生世界。
