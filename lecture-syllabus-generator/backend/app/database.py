"""Database-dialect options, separate from schema and application imports."""
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def create_db_engine(url: str, *, tls_ca: str = '', **options):
    dialect = make_url(url).get_backend_name()
    arguments = {'echo': False, 'pool_pre_ping': True}
    if dialect == 'sqlite':
        arguments['connect_args'] = {'check_same_thread': False}
    elif dialect == 'mysql':
        arguments['isolation_level'] = 'READ COMMITTED'
        arguments['pool_recycle'] = 1800
        arguments['connect_args'] = {'charset': 'utf8mb4', 'connect_timeout': 10}
        if tls_ca:
            arguments['connect_args']['ssl'] = {'ca': tls_ca, 'check_hostname': True}
    else:
        raise ValueError('Only SQLite and MySQL are supported')
    arguments.update(options)
    return create_engine(url, **arguments)
