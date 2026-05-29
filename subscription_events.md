# Subscription Events

> **AI Instruction:** When referencing information from this file, always prefix with "According to subscription-events.md" so the user knows the source.

## Overview

The ISY controller uses a UPnP-based subscription event system to notify connected clients (Admin Console, websocket subscribers) of state changes. Events are categorized by **control** (event category) and **action** (specific event within that category).

Events are fired from the firmware via `UpdateLogicalDevice()` and delivered to subscribers as XML with the structure:
```xml
<event control="{control}" action="{action}" node="{nodeAddress}">
  <eventInfo>...</eventInfo>
</event>
```

The subscription path format is: `events/controls/{control}/{action}`

---

## Source Files

| File | Role |
|------|------|
| `ISY/src/system/UDEvents.h` | All event category and action constants (firmware) |
| `ISYFinder/.../device/model/IModelChangeListener.java` | Client-side event constants and callbacks |
| `ISYFinder/.../upnp/UDControlPoint.java` | Client-side event dispatch (`deviceStateChanged()`) |
| `ISY/src/u7/U7Report.cpp` | Fires node-related events (`_3`) |
| `ISY/src/u7/U7SystemEditors.cpp` | Fires system editor event (`_24`) |

---

## Event Delivery Mechanism

Events are sent via `UpdateLogicalDevice()` (`ISY/src/dev/UDLogicalDeviceUpdater.cpp`). Variants:
- `UpdateLogicalDevice(control, action, node, eventInfo, sid)` — standard event
- `UpdateLogicalDeviceUom(control, action, node, uom, prec, sid)` — status with UOM
- `UpdateLogicalDeviceCurrValue(control, currValue, node, sid)` — current value update

The `sid` parameter targets a specific subscriber (or `NUMBER_NOT_SET` for all subscribers).

Clients subscribe via UPnP SUBSCRIBE or websocket connection. Subscriptions expire and must be renewed.

---

## Node Status/Control Events (Driver Controls)

The most common events. Fired when a device's status changes (on/off, level, temperature, etc.). These use the **driver control name** as the control field — NOT a `_X` category.

Event format:
```xml
<event control="{driverControlName}" action="{value}" node="{nodeAddress}">
  <eventInfo uom="{uom}" prec="{precision}" />
</event>
```

Common driver control names:

| Control | Meaning |
|---------|---------|
| `ST` | Status (on/off level, 0-255) |
| `OL` | On Level |
| `RR` | Ramp Rate |
| `CLISPH` | Thermostat heat setpoint |
| `CLISPC` | Thermostat cool setpoint |
| `CLIMD` | Thermostat mode |
| `CLIFS` | Thermostat fan state |
| `BATLVL` | Battery level |
| `ERR` | Error status |

Driver controls are defined per-nodedef in the profile. The control name comes from `DriverControl::GetName()`. Fired by `LogicalDevice::HandleDeviceResponse()` → `UpdateLogicalDeviceCurrValue()` or `UpdateLogicalDeviceUom()`.

---

## System Event Categories (`_X`)

### `_0` — Heartbeat

| Action | Constant | Description |
|--------|----------|-------------|
| (none) | `DEVINTIX_CLIENT_HEART_BEAT` | Periodic keepalive sent to subscribers |

---

### `_1` — Trigger/Program Events

| Action | Constant | Description |
|--------|----------|-------------|
| `0` | `UD_TRIGGER_EVENT_STATUS` | Program status changed |
| `1` | `UD_TRIGGER_EVENT_GET_STATUS` | Get program status |
| `2` | `UD_TRIGGER_EVENT_KEY_CHANGED` | Program key changed |
| `3` | `UD_TRIGGER_EVENT_INFO_STRING` | Info string |
| `4` | `UD_TRIGGER_EVENT_LEARN_IR` | Learn IR |
| `5` | `UD_TRIGGER_EVENT_SCHEDULE` | Schedule changed |
| `6` | `UD_TRIGGER_EVENT_VAR_STATUS` | Variable status changed |
| `7` | `UD_TRIGGER_EVENT_VAR_INIT` | Variable initialized |
| `8` | `UD_TRIGGER_EVENT_CURRENT_KEY` | Current key |
| `9` | `UD_TRIGGER_EVENT_VAR_UPDATED` | Variable updated |

---

### `_2` — Device Specific Events

| Action | Constant | Description |
|--------|----------|-------------|
| (varies) | `UD_DEVICE_SPECIFIC_EVENT` | Protocol-specific events (Insteon, etc.) |
| `UD2` | `UD_UD2_EVENT` | UD2 event (special case) |

---

### `_3` — Nodes Updated Events

The most commonly used event category. Fired by `U7Report` and device drivers.

#### Node Actions

| Action | Constant | Description | Fired By |
|--------|----------|-------------|----------|
| `NN` | `DEVINTIX_NODE_RENAMED_ACTION` | Node renamed | Node manager |
| `NR` | `DEVINTIX_NODE_REMOVED_ACTION` | Node removed | Node manager |
| `ND` | `DEVINTIX_NODE_ADDED_ACTION` | Node added | Node manager |
| `RV` | `DEVINTIX_NODE_REVISED_ACTION` | Node revised (flags changed) | `U7Report::reportNodeRevised()` |
| `NI` | `DEVINTIX_NODE_SUPPORTED_TYPE_INFO_CHANGED_ACTION` | Node's nodedef changed | `U7Report::reportNodeDefChanged()` |
| `NE` | `DEVINTIX_NODE_IN_ERROR_ACTION` | Node in error (comm failure) | Node manager |
| `CE` | `DEVINTIX_NODE_CLEAR_ERROR_ACTION` | Node error cleared | Node manager |
| `EN` | `DEVINTIX_NODE_ENABLED_ACTION` | Node enabled/disabled | Node manager |
| `PC` | `DEVINTIX_NODE_PARENT_CHANGED_ACTION` | Node parent changed | Node manager |
| `NX` | `DEVINTIX_NODE_NOTES_CHANGED_ACTION` | Node notes changed | Node manager |
| `PI` | `DEVINTIX_NODE_POWER_INFO_CHANGED_ACTION` | Power info changed | Node manager |
| `DI` | `DEVINTIX_NODE_DEVICE_ID_CHANGED` | Device ID changed | Node manager |
| `AA` | `DEVINTIX_NODE_ALL_NODES_ADDED` | All nodes for a device added (bulk) | `U7Report::reportAllNodesAdded()` |
| `MV` | `DEVINTIX_NODE_MOVED_ACTION` | Node moved to group | Node manager |
| `CL` | `DEVINTIX_NODE_CHANGE_LINK_ACTION` | Node link role changed | Node manager |
| `RG` | `DEVINTIX_NODE_REMOVED_FROM_GROUP_ACTION` | Node removed from group | Node manager |
| `WH` | `DEVINTIX_HAS_PENDING_DEVICE_WRITES` | Node has pending device writes | Node manager |
| `WD` | `DEVINTIX_WRITING_TO_DEVICE` | Writing to device | Node manager |

#### Property Actions

| Action | Constant | Description |
|--------|----------|-------------|
| `DP` | `DEVINTIX_NODE_DEVICE_PROPERTY_CHANGED` | Device property changed |
| `LU` | `DEVINTIX_NODE_LINK_UPDATED_ACTION` | Scene link updated |

#### Group/Scene Actions

| Action | Constant | Description |
|--------|----------|-------------|
| `GN` | `DEVINTIX_GROUP_RENAMED_ACTION` | Group renamed |
| `GR` | `DEVINTIX_GROUP_REMOVED_ACTION` | Group removed |
| `GD` | `DEVINTIX_GROUP_ADDED_ACTION` | Group added |

#### Folder Actions

| Action | Constant | Description |
|--------|----------|-------------|
| `FN` | `DEVINTIX_FOLDER_RENAMED_ACTION` | Folder renamed |
| `FR` | `DEVINTIX_FOLDER_REMOVED_ACTION` | Folder removed |
| `FD` | `DEVINTIX_FOLDER_ADDED_ACTION` | Folder added |

#### Discovery Actions

| Action | Constant | Description |
|--------|----------|-------------|
| `SN` | `DEVINTIX_DISCOVERING_NODES_ACTION` | Node discovery started |
| `SC` | `DEVINTIX_DISCOVERING_NODES_COMPLETE_ACTION` | Node discovery complete |
| `WR` | `DEVINTIX_NETWORK_RENAMED_ACTION` | Network renamed |

---

### `_4` — System Configuration Events

| Action | Constant | Description |
|--------|----------|-------------|
| `0` | `UD_TIME_CHANGED_ACTION` | Time changed |
| `1` | `UD_TIME_CONFIG_CHANGED_ACTION` | Time config changed |
| `2` | `UD_NTP_SETTINGS_UPDATED_ACTION` | NTP settings updated |
| `3` | `UD_NOTIFICATION_SETTINGS_UPDATED_ACTION` | Notification settings updated |
| `4` | `UD_NTP_COMM_FAILED_ACTION` | NTP communication failed |
| `5` | `UD_BATCH_MODE_CHANGED` | Batch mode changed |
| `6` | `UD_BATTERY_DEVICE_WRITE_MODE_CHANGED` | Battery device write mode changed |

---

### `_5` — System Busy Events

| Action | Constant | Description |
|--------|----------|-------------|
| `0` | `DEVINTIX_SYSTEM_IS_NOT_BUSY_ACTION` | System not busy |
| `1` | `DEVINTIX_SYSTEM_IS_BUSY_ACTION` | System is busy |
| `2` | `DEVINTIX_SYSTEM_IS_IDLE_ACTION` | System is idle |
| `3` | `DEVINTIX_SYSTEM_IN_SAFE_MODE_ACTION` | System in safe mode |

---

### `_6` — Internet Access Events

| Action | Constant | Description |
|--------|----------|-------------|
| `0` | `DEVINTIX_INTERNET_ACCESS_DISABLED_ACTION` | Internet access disabled |
| `1` | `DEVINTIX_INTERNET_ACCESS_ENABLED_ACTION` | Internet access enabled (URL in eventInfo) |
| `2` | `DEVINTIX_INTERNET_ACCESS_FAILED_ACTION` | Internet access failed |

---

### `_7` — Progress Events

| Action | Constant | Description |
|--------|----------|-------------|
| `1` | `UD_PROGRESS_EVENT_UPDATE` | General progress update |
| `2.1` | `UD_PROGRESS_DEVICE_ADDER_INFO_EVENT` | UPB info during include/exclude |
| `2.2` | `UD_PROGRESS_DEVICE_ADDER_WARN_EVENT` | UPB warning |
| `2.3` | `UD_PROGRESS_DEVICE_ADDER_ERR_EVENT` | UPB error |
| `4.1` | `UD_PROGRESS_UZW_ADDER_INFO_EVENT` | Original Z-Wave info |
| `4.2` | `UD_PROGRESS_UZW_ADDER_WARN_EVENT` | Original Z-Wave warning |
| `4.3` | `UD_PROGRESS_UZW_ADDER_ERR_EVENT` | Original Z-Wave error |
| `12.1` | `UD_PROGRESS_UYZ_ADDER_INFO_EVENT` | ZMatter Z-Wave info |
| `12.2` | `UD_PROGRESS_UYZ_ADDER_WARN_EVENT` | ZMatter Z-Wave warning |
| `12.3` | `UD_PROGRESS_UYZ_ADDER_ERR_EVENT` | ZMatter Z-Wave error |
| `12.4` | `UD_PROGRESS_UYZ_OTA_INFO_EVENT` | ZMatter Z-Wave OTA info |
| `14.1` | `UD_PROGRESS_UYB_ADDER_INFO_EVENT` | ZigBee info |
| `14.2` | `UD_PROGRESS_UYB_ADDER_WARN_EVENT` | ZigBee warning |
| `14.3` | `UD_PROGRESS_UYB_ADDER_ERR_EVENT` | ZigBee error |
| `15.1` | `UD_PROGRESS_UYM_ADDER_INFO_EVENT` | Matter info |
| `15.2` | `UD_PROGRESS_UYM_ADDER_WARN_EVENT` | Matter warning |
| `15.3` | `UD_PROGRESS_UYM_ADDER_ERR_EVENT` | Matter error |

---

### `_8` — Security System Events

| Action | Constant | Description |
|--------|----------|-------------|
| `0` | `UD_SECURITY_SYSTEM_IS_DISCONNECTED_ACTION` | Security system disconnected |
| `1` | `UD_SECURITY_SYSTEM_IS_CONNECTED_ACTION` | Security system connected |
| `DA` | `UD_SECURITY_SYSTEM_STATUS_DISARMED` | Disarmed |
| `AW` | `UD_SECURITY_SYSTEM_STATUS_ARMED_AWAY` | Armed Away |
| `AS` | `UD_SECURTIY_SYSTEM_STATUS_ARMED_STAY` | Armed Stay |
| `ASI` | `UD_SECURITY_SYSTEM_STATUS_ARMD_STAY_INSTANT` | Armed Stay Instant |
| `AN` | `UD_SECURITY_SYSTEM_STATUS_ARMED_NIGHT` | Armed Night |
| `ANI` | `UD_SECURITY_SYSTEM_STATUS_ARMED_NIGHT_INSTANT` | Armed Night Instant |
| `AV` | `UD_SECURITY_SYSTEM_STATUS_ARMED_VACATION` | Armed Vacation |

---

### `_9` — System Alert Events

| Action | Constant | Description |
|--------|----------|-------------|
| `1` | `UD_SYSTEM_ALERT_ELEC_PEAK_DEMAND` | Electricity peak demand |
| `2` | `UD_SYSTEM_ALERT_ELEC_MAX_UTLIZATION` | Electricity max utilization |
| `3` | `UD_SYSTEM_ALERT_GAS_MAX_UTLIZATION` | Gas max utilization |
| `4` | `UD_SYSTEM_ALERT_WATER_MAX_UTILIZATION` | Water max utilization |

---

### `_10` — Electricity/OpenADR Events

| Action | Constant | Description |
|--------|----------|-------------|
| `1` | `UD_INET_POLL_OPEN_DR_ERROR_ACTION` | OpenDR error |
| `2` | `UD_INET_POLL_OPEN_DR_STATUS_ACTION` | OpenDR status |
| `4` | `UD_MOD_ELEC_UTILIZATION_CHANGED_ACTION` | Electricity utilization changed |
| `5` | `UD_INET_POLL_FYP_ERROR_ACTION` | FYP error |
| `6` | `UD_INET_POLL_FYP_STATUS_ACTION` | FYP status |
| `8` | `UD_INET_POLL_OPEN_ADR_2B_REG_ACTION` | OpenADR 2.0b registration |
| `9` | `UD_INET_POLL_OPEN_ADR_2B_REPORT_ACTION` | OpenADR 2.0b report |
| `10` | `UD_INET_POLL_OPEN_ADR_2B_OPT_ACTION` | OpenADR 2.0b opt |
| `11` | `UD_INET_POLL_OPEN_ADR_INEFFICIENT_MANUAL_DIMMER_CHANGE` | Inefficient dimmer change |
| `12` | `UD_INET_POLL_OPEN_ADR_INEFFICIENT_MANUAL_TSTAT_HEAT_CHANGE` | Inefficient thermostat heat change |
| `13` | `UD_INET_POLL_OPEN_ADR_INEFFICIENT_MANUAL_TSTAT_COOL_CHANGE` | Inefficient thermostat cool change |

---

### `_11` — Climate Events

Actions are numeric IDs for climate data points (temperature, humidity, wind, rain, forecasts, etc.). Defined in `UDIncludes/module/climate/UDClimate.h`.

---

### `_12` — AMI Device Events (Smart Meter/SEP)

| Action | Constant | Description |
|--------|----------|-------------|
| `1` | `UD_AMI_DEVICE_NETWORK_STATUS_ACTION` | Network status |
| `2` | `UD_AMI_DEVICE_TIME_STATUS_ACTION` | Time status |
| `3` | `UD_AMI_DEVICE_MESSAGE_START_ACTION` | Message start |
| `31` | `UD_AMI_DEVICE_MESSAGE_SCHEDULED_ACTION` | Message scheduled |
| `4` | `UD_AMI_DEVICE_MESSAGE_STOP_ACTION` | Message stop |
| `5` | `UD_AMI_DEVICE_PRICE_START_ACTION` | Price start |
| `51` | `UD_AMI_DEVICE_PRICE_SCHEDULED_ACTION` | Price scheduled |
| `6` | `UD_AMI_DEVICE_PRICE_STOP_ACTION` | Price stop |
| `7` | `UD_AMI_DEVICE_DRLC_START_ACTION` | DRLC start |
| `71` | `UD_AMI_DEVICE_DRLC_SCHEDULED_ACTION` | DRLC scheduled |
| `8` | `UD_AMI_DEVICE_DRLC_STOP_ACTION` | DRLC stop |
| `9` | `UD_AMI_DEVICE_METER_EVENT` | Meter event |
| `10` | `UD_AMI_DEVICE_METER_FORMAT_EVENT` | Meter format event |
| `110` | `UD_AMI_DEVICE_METER_FASTPOLL_ON_ACTION` | Fast poll on |
| `111` | `UD_AMI_DEVICE_METER_FASTPOLL_OFF_ACTION` | Fast poll off |

---

### `_13` — Electricity Monitor Events

| Action | Constant | Description |
|--------|----------|-------------|
| `1` | `UD_ELECTRICITY_MONITOR_CHANNELS_ACTION` | Channel data |
| `2` | `UD_ELECTRICITY_MONITOR_REPORT_ACTION` | Report data |
| `7` | `UD_ELECTRICITY_MONITOR_RAW_ACTION` | Raw data |

---

### `_14` — UPB Linker Events

| Action | Constant | Description |
|--------|----------|-------------|
| `1` | `UD_UPB_LINKER_EVENT_DEVICE_STATUS` | Device status during linking |
| `2` | `UD_UPB_LINKER_EVENT_PENDING_STOP_FIND` | Stop find pending |
| `3` | `UD_UPB_LINKER_EVENT_PENDING_CANCEL_DEVICE_ADDER` | Cancel device adder pending |

---

### `_15` — UPB Device Adder State

Actions are numeric states defined in `UPBDeviceAdder.h` (finding devices, adding found devices, idle, not running).

---

### `_16` — UPB Status Events

| Action | Constant | Description |
|--------|----------|-------------|
| `1` | `UD_UPB_STATUS_EVENT_DEVICE_SIG_REPORT` | Device signal report |
| `2` | `UD_UPB_STATUS_EVENT_DEVICE_SIG_REPORTS_REMOVED` | Signal reports removed |
| `3` | `UD_UPB_STATUS_EVENT_LINK_DEFAULT_CHANGED` | Link default changed |

---

### `_17` — Gas Meter Events

| Action | Constant | Description |
|--------|----------|-------------|
| `1` | `UD_GAS_METER_STATUS_ACTION` | Gas meter status |
| `2` | `UD_GAS_METER_ERROR_ACTION` | Gas meter error |

---

### `_18` — ZigBee Events (Legacy Driver)

| Action | Constant | Description |
|--------|----------|-------------|
| `1` | `UD_ZIGBEE_STATUS_ACTION` | Network status |

---

### `_19` — Elk Security Panel Events

Sub-categories: Topology (1), Area (2), Zone (3), Keypad (4), Output (5), System Status (6), Thermostat (7). Detailed actions defined in `UDEvents.h`.

---

### `_20` — Linker Events (Generic Device Linking)

| Action | Constant | Description |
|--------|----------|-------------|
| `1` | `UD_LINKER_EVENT_DEVICE_STATUS` | Status info for device being linked |
| `2` | `UD_LINKER_EVENT_CLEAR` | Device list table cleared |

---

### `_21` — Original Z-Wave Events (`UD_UZW_EVENT`)

Sub-categories: System Status (1), Discovery Status (2), General Status (3), General Error (4). Used by the legacy Z-Wave driver (pre-ZMatter). See `_25` for the current ZMatter Z-Wave driver.

---

### `_22` — Billing Events

| Action | Constant | Description |
|--------|----------|-------------|
| `1` | `UD_BILLING_EVENT_COST_CHANGED` | Electricity cost changed |

---

### `_23` — Portal Events

| Action | Constant | Description |
|--------|----------|-------------|
| `1` | `UD_PORTAL_EVENT_CHANGED` | Portal configuration changed |

---

### `_24` — System Editor Events

| Action | Constant | Description | Fired By |
|--------|----------|-------------|----------|
| `1` | `UD_SYSTEM_EDITOR_CHANGED` | System editor `_sys_notify_short` changed | `ISY/src/u7/U7SystemEditors.cpp` |

**Note:** This is fired when notification system editors are updated. The node field contains the editor name (e.g., `_sys_notify_short`). Triggered by `/rest/report/notifications/updated`.

---

### `_25` — ZMatter Z-Wave Events (`UD_UYZ_EVENT`)

Fired by `UYZEventManager::sendUDZWayEvent()`. Action format: `"{category}.{type}"`. See `zwave.md` for full sub-action details.

Sub-categories: System Status (1), Discovery Status (2), General Status (3), General Error (4), S2 Process (5), OTA Firmware Upgrade (6), Backup/Restore (7), Device Interview (8), Button Detect (9), Logger (10)

---

### `_26` — System Upgrade Events

| Action | Constant | Description |
|--------|----------|-------------|
| `1` | `UD_SYS_UPGRADE_ACTIVE` | Upgrade in progress |
| `2` | `UD_SYS_UPGRADE_INACTIVE` | Upgrade not active |
| `3` | `UD_SYS_UPGRADE_AVAILABLE` | Upgrade available |
| `4` | `UD_SYS_UPGRADE_REBOOT` | Reboot required |

---

### `_27` — ZigBee Events (`UD_UYB_EVENT`)

Same structure as `_25`. Sub-categories: System Status (1), Discovery Status (2), General Status (3), General Error (4), S2 Process (5), OTA Firmware Upgrade (6), Backup/Restore (7), Device Interview (8), Button Detect (9)

---

### `_28` — Matter Events / Profiles Events (SHARED)

This category is shared between Matter (UYM) system events and Profile change events.

#### Matter Events (ACTIVE — fired by `UDIncludes/productDriver/udzmat/UYMEventManager.h`)

| Action | Constant | Description |
|--------|----------|-------------|
| 1 | `UD_UYM_SYSTEM_STATUS` | Matter system status (enabled/connected/dongle updated) |
| 2 | `UD_UYM_DISCOVERY_STATUS` | Matter discovery status |
| 3 | `UD_UYM_RX_TX` | RX data available |
| 8 | `UD_UYM_DEVICE_INTERVIEW` | Device interview status |

#### Profile Events (DEFINED BUT NEVER FIRED)

| Action | Constant | Description |
|--------|----------|-------------|
| 1 | `UD_PROFILES_PROFILE_UPDATED` | Profile updated |
| 2 | `UD_PROFILES_PROFILE_DELETED` | Profile deleted |
| 3 | `UD_PROFILES_EDITOR_UPDATED` | Editor updated |
| 4 | `UD_PROFILES_EDITOR_DELETED` | Editor deleted |
| 5 | `UD_PROFILES_NODEDEF_UPDATED` | NodeDef updated |
| 6 | `UD_PROFILES_NODEDEF_DELETED` | NodeDef deleted |
| 7 | `UD_PROFILES_LINKDEF_UPDATED` | LinkDef updated |
| 8 | `UD_PROFILES_LINKDEF_DELETED` | LinkDef deleted |

> **Warning:** The profile events (actions 1–8) are defined in `UDEvents.h` but NO code in the firmware ever fires them. They were added in December 2024 as placeholders. Do not tell users to listen for these events — they will never arrive.

---

## Client-Side Event Handling

Events are received by `UDControlPoint.deviceStateChanged()` which dispatches based on the control field:

| Control | Handler Method |
|---------|---------------|
| `_1` | `onTriggerStatus()` |
| `_2` | `onDeviceSpecific()` |
| `_3` | `nodesChanged()` → dispatches by action |
| `_4` | `onSystemConfigChanged()` |
| `_5` | `onSystemStatus()` |
| `_6` | `onInternetAccessUpdated()` |
| `_7` | `onProgress()` |
| `_20` | `onLinkerEvent()` |

---

## Key Event: `_3/NI` — NodeDef Changed

This is the primary event for profile-related changes. See `profiles.md` for full details.

**Fired when:** A node's nodedef assignment changes (via `changeNode()` in node server, or device driver detecting capability changes).

**NOT fired when:** Profile database is updated via `/rest/profiles/.../update`, `/rest/profiles/.../move`, or at startup migration. These operations modify the profile definitions but do not notify clients.

**Client callback:** `IModelChangeListener.onNodeSupportedTypeInfoChanged(device, nodeAddress)`

---

## Troubleshooting

### Events Not Being Received

1. **Check subscription is active** — subscriptions expire and must be renewed
2. **Check the specific event is actually fired** — many events defined in `UDEvents.h` are never used (e.g., `_28` profile events)
3. **Check the event path** — format is `controls/{control}/{action}`, e.g., `controls/_3/NI`

### Profile Changes Not Triggering Events

See the "Subscription Events" section in `profiles.md`. The profile update/move operations do NOT fire events. Only per-node `changeNode()` calls fire `_3/NI`.