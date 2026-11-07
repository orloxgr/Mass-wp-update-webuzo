"""Concise causes for the panel, retaining the complete original failure."""
import re


def decorate(record):
    raw = record.get('error')
    if not isinstance(raw, str) or not raw:
        return record
    summary = raw.splitlines()[0].strip()
    database = re.search(r'mysqli|wpdb|wp-db\.php|database export|DB_HOST', raw, re.I)
    if database and re.search(r'getaddrinfo|php_network_getaddresses', raw, re.I):
        match = re.search(r'getaddrinfo for ([^\s]+) failed', raw, re.I)
        host = (' ' + match[1]) if match else ''
        summary = 'Cannot resolve database host' + host + '. Check DB_HOST in wp-config.php and DNS.'
    elif re.search(r'Call to undefined function exec\(\)', raw, re.I):
        summary = 'Database export failed: exec() is disabled in this PHP CLI process.'
    elif database and re.search(r'Access denied for user', raw, re.I):
        summary = 'Database authentication failed. Check the database credentials and permissions.'
    elif database and re.search(r'Unknown database', raw, re.I):
        summary = 'The configured database does not exist. Check DB_NAME in wp-config.php.'
    elif database and re.search(r'Connection refused', raw, re.I):
        summary = 'Database connection refused. Check the database service, host and port.'
    else:
        summary = summary.split('Stack trace:', 1)[0].strip()
    # Shorten only the presentation; retain the original recorded failure.
    if len(summary) > 240: summary = summary[:237] + '…'
    record['error_summary'] = summary
    return record
