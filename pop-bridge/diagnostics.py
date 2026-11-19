"""Bounded, categorical POP failure notices. No raw provider text is displayed."""
import json
import account_state as S
import bridge_config as C

PHASES = {'local-request': 'processing the local request',
          'connect': 'connecting to the provider', 'user': 'submitting the username',
          'pass': 'submitting the password', 'stat': 'checking mailbox totals',
          'uidl': 'listing message identifiers', 'list': 'listing message sizes',
          'retr': 'retrieving a message', 'rset': 'resetting the provider session',
          'quit': 'closing the provider session'}
DETAILS = {'connection_closed': 'The connection closed unexpectedly.',
           'response_line_too_long': 'A response line exceeded the POP parser limit.',
           'server_error_response': 'The provider returned a negative response.',
           'protocol_error': 'A POP protocol error occurred.',
           'timeout': 'The operation timed out.', 'tls_error': 'A TLS error occurred.',
           'transport_error': 'A transport error occurred.',
           'other_error': 'An internal or validation error occurred.'}


def validate(data):
    S.need(type(data) is dict and set(data) == {'phase', 'detail', 'login'} and
           type(data['phase']) is str and data['phase'] in PHASES and
           type(data['detail']) is str and data['detail'] in DETAILS and
           data['login'] in ('yes', 'no'), 'failure_notice_invalid')
    S.need(not (data['login'] == 'yes' and data['phase'] in ('connect', 'user', 'pass')),
           'failure_notice_inconsistent')
    return data


def record(run, report):
    """Copy only validated categories from this worker's exact transaction."""
    fields = {}
    for line in S.read_private(report, 8192).decode('ascii').splitlines():
        key, value = line.split('=', 1)
        S.need(key not in fields, 'duplicate_failure_field')
        fields[key] = value
    S.need(fields.get('status') == 'failed', 'failure_report_not_failed')
    data = validate(dict(phase=fields.get('issue_phase'),
                         detail=fields.get('failure_detail'),
                         login=fields.get('login_completed')))
    S.new_file(run/'failure.json', (json.dumps(data)+'\n').encode())


def describe(run):
    try:
        data = validate(json.loads(S.read_private(run/'failure.json', 1024),
                                   object_pairs_hook=C.unique_object))
    except (OSError, ValueError, TypeError, RecursionError):
        return None
    prefix = ('Provider login succeeded, but ' if data['login'] == 'yes'
              else 'Provider login was not completed; ')
    text = prefix + PHASES[data['phase']] + ' failed. ' + DETAILS[data['detail']]
    if data['login'] == 'yes':
        text += ' Agent may incorrectly describe this as a username/password rejection.'
    return text
