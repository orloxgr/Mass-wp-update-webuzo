"""PHP options confined to the account-owned WP-CLI database export process."""

DISABLED_FUNCTIONS = "echo json_encode(ini_get('disable_functions'));"
EXPORT_FUNCTIONS = frozenset(('exec', 'proc_open', 'proc_close', 'proc_get_status'))


def database_flags(disabled):
    if not isinstance(disabled, str):
        raise ValueError('Could not read the database export PHP CLI configuration')
    functions = [name.strip() for name in disabled.split(',') if name.strip()]
    # Older WP-CLI uses exec(); newer versions use its Process helper. Restore
    # their process dependencies for this CLI invocation, retaining every other
    # restriction. Never edit php.ini or FPM pools.
    if not any(name.lower() in EXPORT_FUNCTIONS for name in functions):
        return []
    return ['-d', 'disable_functions=' + ','.join(name for name in functions if name.lower() not in EXPORT_FUNCTIONS)]
