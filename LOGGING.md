# JobsBot Structured JSON Logging Guide

## Overview

All JobsBot components (Scraper, Bot, Dispatcher) now use professional structured JSON logging. Logs are automatically formatted as JSON and sent to stdout, which flows through your logging infrastructure (Filebeat → Logstash → Elasticsearch → Kibana).

## Architecture

```
Lambda/Docker stdout (JSON logs)
    ↓
Filebeat (CloudWatch or Docker Logs)
    ↓
Logstash (port 30092)
    ↓
Elasticsearch (daily indices: bot-logs-YYYY.MM.dd)
    ↓
Kibana (port 30056)
```

## Log Format

Each log entry is emitted as JSON with the following structure:

```json
{
  "timestamp": "2026-05-20T10:30:45.123456Z",
  "level": "INFO",
  "message": "New jobs found",
  "component": "scraper",
  "correlation_id": "abc123",
  "job_id": "456",
  "chat_id": "789",
  "execution_id": "xyz789abc",
  "environment": "lambda",
  "additional_field": "value"
}
```

### Standard Fields

| Field | Description | Always Present |
|-------|-------------|-----------------|
| `timestamp` | ISO 8601 UTC timestamp | Yes |
| `level` | Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) | Yes |
| `message` | Primary log message | Yes |
| `component` | Component name (scraper, bot, dispatcher) | Yes |
| `environment` | Execution environment (lambda, docker, development) | Yes |
| `execution_id` | AWS Lambda request ID | Lambda only |
| `container_id` | Docker container ID (short form) | Docker only |
| `correlation_id` | Request/job correlation identifier | Optional |
| `job_id` | LinkedIn job ID | Optional |
| `chat_id` | Telegram user chat ID | Optional |
| `user_id` | User identifier | Optional |

## Usage

### Basic Logging

```python
from shared.logging import get_logger

logger = get_logger('my_component')

# Log a simple message
logger.info("Processing started")
logger.warning("Something unusual happened")
logger.error("An error occurred", extra={"error_code": 123})
```

### Logging with Context

Add structured fields using the `extra` parameter:

```python
logger.info("Job scraped", extra={
    "job_id": "12345",
    "company": "TechCorp",
    "duration_seconds": 2.5,
    "chat_id": "9876543"
})
```

This produces:
```json
{
  "timestamp": "2026-05-20T10:30:45.123456Z",
  "level": "INFO",
  "message": "Job scraped",
  "component": "scraper",
  "job_id": "12345",
  "company": "TechCorp",
  "duration_seconds": 2.5,
  "chat_id": "9876543",
  "execution_id": "aws-lambda-request-id",
  "environment": "lambda"
}
```

### Thread-Local Context

For values that should appear in all logs within a scope (e.g., correlation_id), use the context manager:

```python
logger.set_context(correlation_id="batch_123", job_id="456")

# All subsequent logs in this thread include correlation_id and job_id
logger.info("Processing job")  # Includes correlation_id and job_id
logger.error("Failed to save")  # Includes correlation_id and job_id

# Clear context when done
logger.clear_context()
```

Or use the context manager for automatic cleanup:

```python
with logger.contextualize(correlation_id="batch_123"):
    logger.info("Processing")  # Includes correlation_id
    logger.error("Failed")      # Includes correlation_id
# Context automatically cleared here
```

## Kibana Queries & Dashboards

### Common Queries

**Filter by Component:**
```
component:"scraper"
```

**Filter by Log Level:**
```
level:"ERROR"
```

**Filter by Job:**
```
job_id:"789456"
```

**Filter by User:**
```
chat_id:"9876543"
```

**Find All Errors for a User:**
```
level:"ERROR" AND chat_id:"9876543"
```

**Timeline of Scraping Activity:**
```
component:"scraper" AND event:"new_jobs_found"
```

**Lambda Execution Traces:**
```
component:"scraper" AND execution_id:"aws-request-id"
```

**Performance Analysis (Scraping Duration):**
```
component:"scraper" AND duration_seconds:*
```

### Dashboard Ideas

1. **Health Dashboard:**
   - Error rate by component (ERROR count over time)
   - Logs by level (pie chart)
   - Unique users active (by chat_id count)

2. **Scraper Dashboard:**
   - Jobs found per batch (new_jobs_found event)
   - Average scraping duration (duration_seconds metric)
   - Error timeline (ERROR level logs)
   - Top errors (error message frequency)

3. **User Activity Dashboard:**
   - Active users (unique chat_id)
   - Subscription creations over time
   - CV uploads over time (CV_UPLOAD_COUNTER)
   - Command invocations (/start count)

4. **Performance Dashboard:**
   - Lambda execution times (execution_id tracing)
   - AI generation latency (duration_seconds for AI operations)
   - Database operation latency
   - P95/P99 latency percentiles

## Events

### Structured Events

Some log entries represent structured events that are easier to query:

```python
# New jobs found event
logger.info("New jobs found", extra={
    "event": "new_jobs_found",
    "count": 5,
    "jobs": [{"id": "123", "title": "Engineer"}, ...],
    "chat_id": "9876543"
})
```

Query for events:
```
event:"new_jobs_found"
```

## Best Practices

### Do

✅ Include relevant context fields to make logs searchable:
```python
logger.info("Subscription created", extra={
    "subscription_id": sub_id,
    "chat_id": chat_id,
    "frequency_minutes": frequency
})
```

✅ Use appropriate log levels:
- `DEBUG`: Detailed diagnostic information
- `INFO`: General informational messages
- `WARNING`: Something unexpected but not critical
- `ERROR`: An error occurred but execution continues
- `CRITICAL`: A serious error; execution may stop

✅ Include operation duration for performance analysis:
```python
start = time.time()
result = do_work()
logger.info("Work completed", extra={
    "duration_seconds": round(time.time() - start, 2)
})
```

### Don't

❌ Include sensitive data (PII, passwords, API keys):
```python
# Bad
logger.info("User info", extra={"email": user_email, "password": pwd})

# Good
logger.info("User authenticated", extra={"user_id": user_id})
```

❌ Log redundant information that's in the message:
```python
# Bad
message = f"Error: {error}"
logger.error(message, extra={"error": error})

# Good
logger.error("Operation failed", extra={"error_code": error.code})
```

## Migration from print() to logger

### Before (print statements)
```python
print(f"ERROR - Failed to process: {e}")
print(f"DEBUG - Job scraped in {duration:.2f}s")
print(json.dumps({"event": "new_jobs_found", "count": 5}))
```

### After (structured logging)
```python
logger.error("Failed to process", extra={"error": str(e)})
logger.info("Job scraped", extra={"duration_seconds": duration})
logger.info("New jobs found", extra={"event": "new_jobs_found", "count": 5})
```

## Troubleshooting

### Logs not appearing in Kibana

1. **Check Filebeat status:**
   - Verify Filebeat is reading CloudWatch logs (Lambda) or Docker logs (Bot)
   - Check Filebeat is connected to Logstash on port 30092

2. **Check Logstash:**
   - Verify logs reach Elasticsearch with correct index pattern
   - Check Logstash is parsing JSON correctly

3. **Check Elasticsearch:**
   ```bash
   # Get index stats
   curl http://localhost:9200/_cat/indices?v
   
   # Query recent logs
   curl http://localhost:9200/bot-logs-*/_search?pretty
   ```

4. **Check Kibana:**
   - Verify data view is configured for `bot-logs-*` index pattern
   - Create new data view if needed: Management → Data Views → Create

### Fields not searchable

1. Elasticsearch needs field mappings to understand field types
2. Check field mapping: Kibana → Stack Management → Index Management → bot-logs-* → Mappings
3. Re-index if field types changed:
   ```bash
   POST _reindex
   {
     "source": {"index": "bot-logs-old"},
     "dest": {"index": "bot-logs-new"}
   }
   ```

### Context fields not appearing

1. Ensure context is set before logging:
   ```python
   logger.set_context(job_id="123")
   logger.info("Processing")  # Will include job_id
   ```

2. Clear old context if values persist:
   ```python
   logger.clear_context()
   ```

## Configuration

The logging module auto-detects environment:
- **Lambda:** `environment="lambda"`, includes `execution_id` from `AWS_REQUEST_ID`
- **Docker:** `environment="docker"`, includes `container_id` from hostname
- **Development:** `environment="development"`

Default log level is `INFO` for all environments. To change:

```python
import logging

# Set to DEBUG (within your component)
logger._logger.setLevel(logging.DEBUG)

# Or for all loggers
logging.getLogger().setLevel(logging.DEBUG)
```

## Example: Full Integration

```python
from shared.logging import get_logger
import time

logger = get_logger('my_component')

def process_job(job_id, chat_id):
    with logger.contextualize(job_id=job_id, chat_id=chat_id):
        try:
            logger.info("Processing started")
            
            start = time.time()
            result = do_work()
            duration = time.time() - start
            
            logger.info("Processing completed", extra={
                "duration_seconds": round(duration, 2),
                "success": True
            })
            return result
            
        except Exception as e:
            logger.error("Processing failed", extra={
                "error": str(e),
                "error_type": type(e).__name__
            })
            raise
```

## Additional Resources

- [python-json-logger documentation](https://github.com/mwhite/python-json-logger)
- [Elasticsearch Query DSL](https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl.html)
- [Kibana Documentation](https://www.elastic.co/guide/en/kibana/current/index.html)
