# ISY Controller Node Property History Endpoint

## Overview

The `/rest/history/node/properties/get` endpoint on the ISY controller firmware provides access to historical node property data. This document explains the complete flow from REST request to database response.

Turn node property history on/off using the following.  The setting persists over IoX restarts. Default is on as of firmware 6.0.4.

/rest/history/node/properties/recording/on
/rest/history/node/properties/recording/off

## REST Endpoint Details

- **URL**: `/rest/history/node/properties/get`
- **Method**: GET
- **Parameters**:
  - `node` - Comma-separated list of node addresses (optional)
  - `property` - Comma-separated list of property IDs (optional)
  - `start` - Start timestamp (optional)
  - `end` - End timestamp (optional)
  - `onebefore` - Include one record before start timestamp (optional)
  - `oneafter` - Include one record after end timestamp (optional)
  - `limit` - Maximum number of records to return (optional, default -1 = all)
  - `sort` - Sort order: "ASC" or "DESC" (optional, default ASC)

  
Here's a sample query, all parameters are optional.  You can specify multiple nodes and/or properties (as comma separated lists)
/rest/history/node/properties/get?property=CLIHUM,ST&node=1D%206F%2015%201&start=2023-12-16T21:11:50.939221-08:00&oneBefore=true&end=2023-12-16T21:11:50.939221-08:00&oneAfter=true

## Request Processing Flow

### 1. REST Request Routing

The REST request is routed through the U7 (Universal 7) command processing system:

- **Command ID**: `U7_CMD_HISTORY_NODE_PROPERTIES_GET` (63)
- **Handler**: `U7ProcessCommand::getNodePropertiesHistory()`
- **Location**: `ISY/src/u7/U7ProcessCommand.cpp:1446`

### 2. Parameter Processing

In `U7ProcessCommand::getNodePropertiesHistory()`:

1. Parses query parameters:
   - `nodes` - node address filter
   - `properties` - property ID filter
   - `dtStart` - start timestamp
   - `dtEnd` - end timestamp
   - `oneBeforeCStr` - boolean for including record before start
   - `oneAfterCStr` - boolean for including record after end
   - `limitCStr` - record limit
   - `sortCStr` - sort order

2. Validates and converts parameters:
   - Parses timestamps
   - Converts strings to booleans
   - Validates sort order (ASC/DESC)

3. Sets up callback structure `nodeEventLogCallback` with parsed parameters

### 3. Database Query Execution

The request delegates to `sendNodeEventLog()` which calls:

```cpp
bool rc = ud.nodePropertyHistory->api->getNodePropertyHistoryXml(
    *out,
    ec,
    &nothingWritten,
    nodeEventLogCallback.nodes,
    nodeEventLogCallback.properties,
    nodeEventLogCallback.dtStart,
    nodeEventLogCallback.dtEnd,
    nodeEventLogCallback.oneBefore,
    nodeEventLogCallback.oneAfter,
    nodeEventLogCallback.limit,
    nodeEventLogCallback.isDescending
);
```

### 4. IoX Node Property History (IoXNPH) System

The core functionality is handled by the IoXNPH system:

- **Main Class**: `IoXNPH` (`ISY/src/nodePropertyHistory/IoXNPH.h`)
- **API Class**: `IoXNPHApi` (`ISY/src/nodePropertyHistory/IoXNPHApi.h`)
- **SQL Class**: `IoXNPHSQL` (`ISY/src/nodePropertyHistory/IoXNPHSQL.h`)

## Database Architecture

### Database Location
- **Path**: `/var/isy/FILES/CONF/NODEHIST.DB`
- **Type**: SQLite3 database
- **Framework**: Uses `UDXSQLite3` wrapper class

### Database Tables

#### NodePropertyDefinitions
```sql
CREATE TABLE IF NOT EXISTS NodePropertyDefinitions (
    NodeAddress TEXT NOT NULL,
    PropertyId TEXT NOT NULL,
    PropertyName TEXT,
    EventTime INTEGER,
    CONSTRAINT PK_NodePropertyDefinitions PRIMARY KEY (NodeAddress, PropertyId)
);
```

#### NodePropertyValuesHistory
```sql
CREATE TABLE IF NOT EXISTS NodePropertyValuesHistory (
    EventTime INTEGER,
    NodeAddress TEXT,
    PropertyId TEXT,
    UOM INTEGER,
    Precision INTEGER,
    ScaledValue INTEGER,
    FloatValue REAL,
    FormattedValue TEXT,
    AssociatedText TEXT
);
```

### Timestamp Format

The `EventTime` column in both tables stores timestamps as **microseconds since Unix epoch** (January 1, 1970 00:00:00 UTC).

- **Format**: INTEGER representing microseconds (μs) since epoch
- **Example**: `1778696727375635` = 1,778,696,727,375,635 microseconds = approximately 1,778,696,727 seconds = ~2026-05-13

This is different from standard Unix timestamps which use seconds since epoch. To convert to standard Unix time:
- **To seconds**: divide by 1,000,000
- **To milliseconds**: divide by 1,000

The microsecond precision allows for sub-millisecond temporal resolution of property change events.

### Query Execution

The `getNodePropertyHistoryXml()` method in `IoXNPHApi.cpp`:

1. Constructs a complex SQL query joining the two tables
2. Applies filters for node addresses, property IDs, and timestamps
3. Orders results by timestamp (ASC/DESC)
4. Limits results if specified
5. Handles "one before/after" logic for boundary records

## Data Collection (How History is Created)

### Event Listening

The system collects data through event-driven architecture:

- **Event Listener**: `IoXNPHEventQueue` implements `UDNodeEventListener`
- **Registration**: `udGetNodeEventListeners().addListener(this)`
- **Location**: `ISY/src/nodePropertyHistory/IoXNPHEventQueue.cpp`

### Node Event Processing

When a node property changes:

1. `IoXNPHEventQueue::onNodeEvent(UDNodeEvent &evt)` is called
2. Event is validated and filtered:
   - Ignores special properties (prefixed with '_')
   - Ignores empty node addresses
   - Filters out certain properties like "UOM"
3. Event is queued for processing

### Database Insertion

Queued events are processed by a background thread:

- **Method**: `IoXNPHEventQueue::processNodeEvent()`
- **Inserts into**: Both `NodePropertyDefinitions` and `NodePropertyValuesHistory` tables
- **Location**: `ISY/src/nodePropertyHistory/IoXNPHEventQueue.cpp:178`

The insertion uses prepared statements for efficiency:

```cpp
INSERT OR REPLACE INTO NodePropertyDefinitions (...)
INSERT INTO NodePropertyValuesHistory (...)
```

## Response Format

The endpoint returns XML data with the following structure:

```xml
<nodes>
  <node id="node_address">
    <properties>
      <property id="property_id" name="property_name">
        <event timestamp="2024-01-01T12:00:00.000000Z">
          <value uom="unit" precision="digits">
            <scaled>100</scaled>
            <float>1.0</float>
            <formatted>ON</formatted>
          </value>
        </event>
        <!-- additional events -->
      </property>
      <!-- additional properties -->
    </properties>
  </node>
  <!-- additional nodes -->
</nodes>
```

## Configuration and Management

### Enabling/Disabling History

- **Enable**: `U7_CMD_HISTORY_NODE_PROPERTIES_ON`
- **Disable**: `U7_CMD_HISTORY_NODE_PROPERTIES_OFF`
- **Configuration**: Stored in system config via `udConfig.setIsNodePropertiesHistoryEnabled()`

### Database Maintenance

- **Pruning**: `U7_CMD_HISTORY_NODE_PROPERTIES_PRUNE`
- **Automatic Pruning**: Runs periodically (every 25,000 events)
- **Prune Logic**: Removes old records based on configurable retention policy

## Performance Considerations

- **Indexing**: Database uses appropriate indexes for query performance
- **Background Processing**: Event queue processes insertions asynchronously
- **Memory Management**: Uses streaming output to handle large result sets
- **Query Optimization**: Complex queries with proper JOINs and filtering

### Result Set Size Limits

**IMPORTANT**: The API can crash when attempting to return very large result sets (typically >20,000-25,000 records).

**Symptoms**:
- **Empty response**: No content returned at all (not even XML tags)
- **Error response**: `HTTP/1.1 0 OK` with `<RestResponse succeeded="false"><status>0</status><reason code="0" />`
- **Inconsistent behavior**: Works with smaller time ranges (e.g., 24 hours) but fails with larger ones (e.g., 3+ days)
- **Node-specific**: Fails for specific nodes with high event rates while other nodes work fine
- **Normal response for comparison**: Working queries return `HTTP/1.1 200 OK` with `<history>` XML data or `<history />` for no results

**Cause**: When too many records match the query criteria, the system runs out of memory or exceeds response size limits while building the XML output in `IoXNPHApi.cpp` (MyCallback::onNextRow).

**Debug Steps**:

1. **Check if data exists in database**:
   ```sql
   SELECT COUNT(*) FROM NodePropertyValuesHistory
   WHERE NodeAddress = 'n001_oadr3ven' AND PropertyId = 'ST';
   ```

2. **Check if JOIN works** (API requires both tables):
   ```sql
   SELECT COUNT(*) FROM NodePropertyValuesHistory AS A
   JOIN NodePropertyDefinitions AS B
   ON A.NodeAddress = B.NodeAddress AND A.PropertyId = B.PropertyId
   WHERE A.NodeAddress = 'n001_oadr3ven' AND A.PropertyId = 'ST';
   ```

3. **Count records in your time range**:
   ```sql
   -- Convert your ISO timestamp to microseconds first
   -- Example: 2026-05-10T19:40:00-06:00 = 1778280000000000 microseconds
   SELECT COUNT(*) FROM NodePropertyValuesHistory
   WHERE NodeAddress = 'n001_oadr3ven' AND PropertyId = 'ST'
   AND EventTime >= 1778280000000000;
   ```

4. **Check field lengths** (unlikely but possible):
   ```sql
   SELECT MAX(LENGTH(FormattedValue)), MAX(LENGTH(AssociatedText))
   FROM NodePropertyValuesHistory
   WHERE NodeAddress = 'n001_oadr3ven' AND PropertyId = 'ST';
   ```

5. **Test with raw HTTP request** to see actual error:
   ```bash
   printf "GET /rest/history/node/properties/get?property=ST&node=n001_oadr3ven HTTP/1.0\r\nHost: <ip>:8080\r\nAuthorization: Basic <base64>\r\n\r\n" | nc -w 5 <ip> 8080
   ```

**Solution**: Use the `limit` parameter to restrict the number of records returned:
```
/rest/history/node/properties/get?node=n001_oadr3ven&property=ST&start=2026-05-10T00:00:00-06:00&limit=1000
```

**Recommendation**: For nodes with frequent updates (high event rates), always use:
- Smaller time ranges (24-48 hours instead of days/weeks)
- The `limit` parameter to cap results at 1000-5000 records
- Pagination by using `start` and `end` parameters to retrieve data in chunks

**Example**: A node with ~163 updates/hour will generate:
- 24 hours: ~3,900 records ✓ (works)
- 3 days: ~27,500 records ✗ (crashes)
- Breaking point: ~20,000-25,000 records

## Error Handling

- **Database Errors**: Logged and returned as HTTP error codes
- **Invalid Parameters**: Returns `U7_RC_MALFORMED_REQUEST` (400)
- **Missing Data**: Returns appropriate empty responses with `<history />` tag
- **Large Result Sets**: Returns error with `status=0, reason code=0` (should be improved to return proper error)
- **Thread Safety**: Uses critical sections for database operations

## Related Components

- **U7 System**: Command processing framework
- **UDXSQLite3**: Database abstraction layer
- **UDNodeEvent**: Node event system
- **IoXShutdownHandler**: Clean shutdown handling
- **UDXTimestamp**: Timestamp utilities

## Files Involved

### Core Implementation
- `ISY/src/u7/U7ProcessCommand.cpp` - REST command handler
- `ISY/src/nodePropertyHistory/IoXNPH*.cpp/.h` - History system
- `ISY/src/u7/U7Constants.h` - Command definitions

### Database
- `UDXRun/src/UDIncludes/DB/UDXSQLite3.h` - Database wrapper
- `UDXRun/src/UDIncludes/DB/UDXSQLUtil.h` - SQL utilities

### Events
- `UDXRun/src/UDIncludes/dev/UDNodeEventListener.h` - Event system
- `ISY/src/events/IoXPublishEvent.cpp` - Event publishing

This system provides comprehensive historical tracking of node property changes while maintaining performance through efficient database design and asynchronous processing.</content>
<parameter name="filePath">/Users/javierrefuerzo/development/iox/udi/debug/node-history.md