import datetime
import os
import threading

from paramiko.pkey import PKey
from paramiko.rsakey import RSAKey
from paramiko.sftp_client import SFTPClient
from paramiko.transport import Transport
from pytest import fixture, yield_fixture

from geofront.keystore import format_openssh_pubkey
from geofront import server
from .sftpd import start_server


# By default it's a minute, but a minute is enough to make the test suite
# very slow.  For faster unit testing we shorten this constant.
server.AUTHORIZATION_TIMEOUT = datetime.timedelta(seconds=5)


def env_default(env):
    return {'default': os.environ[env]} if env in os.environ else {}


def pytest_addoption(parser):
    parser.addoption('--sshd-port-min',
                     metavar='PORT',
                     type=int,
                     default=12220,
                     help='the minimum unused port number [%default(s)]')
    parser.addoption('--sshd-port-max',
                     metavar='PORT',
                     type=int,
                     default=12399,
                     help='the maximum unused port number [%default(s)]')
    parser.addoption('--sshd-state-timeout',
                     metavar='SECONDS',
                     type=int,
                     default=30,
                     help='the maximum seconds to wait for satisfying '
                          'condition of sshd server state  [%default(s)]')
    parser.addoption('--redis-host',
                     metavar='HOSTNAME',
                     help='redis host',
                     **env_default('REDIS_HOST'))
    parser.addoption('--redis-port',
                     metavar='PORT',
                     type=int,
                     default=6379,
                     help='redis port [%default(s)]')
    parser.addoption('--redis-password',
                     metavar='PASSWORD',
                     default=None,
                     help='redis password')
    parser.addoption('--redis-db',
                     metavar='DB',
                     type=int,
                     default=1,
                     help='redis db number [%(default)s]')
    parser.addoption('--postgresql-host',
                     metavar='HOSTNAME',
                     help='postgresql database server host [%(default)s]',
                     **env_default('PGHOST'))
    parser.addoption('--postgresql-port',
                     metavar='PORT',
                     type=int,
                     help='postgresql database server port [%(default)s]',
                     **env_default('PGPORT'))
    parser.addoption('--postgresql-user',
                     metavar='USER',
                     help='postgresql user [%(default)s]',
                     **env_default('PGUSER'))
    parser.addoption('--postgresql-password',
                     metavar='PASSWORD',
                     help='postgresql user password [%(default)s]',
                     **env_default('PGPASSWORD'))
    parser.addoption('--postgresql-database',
                     metavar='DBNAME',
                     help='postgresql database name [%(default)s]',
                     **env_default('PGDATABASE'))
    parser.addoption('--mysql-host',
                     metavar='HOSTNAME',
                     help='mysql database server host [%(default)s]',
                     **env_default('MYSQL_HOST'))
    parser.addoption('--mysql-port',
                     metavar='PORT',
                     type=int,
                     help='mysql database server port [%(default)s]',
                     **env_default('MYSQL_PORT'))
    parser.addoption('--mysql-user',
                     metavar='USER',
                     help='mysql user [%(default)s]',
                     **env_default('MYSQL_USER'))
    parser.addoption('--mysql-passwd',
                     metavar='PASSWD',
                     help='mysql user password [%(default)s]',
                     **env_default('MYSQL_PASSWD'))
    parser.addoption('--mysql-database',
                     metavar='DATABASE',
                     help='mysql database name [%(default)s]',
                     **env_default('MYSQL_DATABASE'))
    parser.addoption('--github-access-token',
                     metavar='TOKEN',
                     help='github access token for key store test (caution: '
                          'it will remove all ssh keys of the account)',
                     **env_default('GITHUB_ACCESS_TOKEN'))
    parser.addoption('--github-org-login',
                     metavar='LOGIN',
                     help='github org login for team test',
                     **env_default('GITHUB_ORG_LOGIN'))
    parser.addoption('--github-team-slugs',
                     metavar='SLUGS',
                     help='space-separated github team slugs for group '
                          'listing test',
                     **env_default('GITHUB_TEAM_SLUGS'))
    parser.addoption('--aws-access-key',
                     metavar='KEY',
                     help='aws access key for master key store test',
                     **env_default('AWS_ACCESS_KEY'))
    parser.addoption('--aws-secret-key',
                     metavar='SECRET',
                     help='aws secret key for master key store test',
                     **env_default('AWS_SECRET_KEY'))
    parser.addoption('--aws-s3-bucket',
                     metavar='BUCKET',
                     help='aws s3 bucket to be used for master key store test',
                     **env_default('AWS_S3_BUCKET'))


def pytest_assertrepr_compare(op, left, right):
    if op == '==' and isinstance(left, PKey) and isinstance(right, PKey):
        left_key = format_openssh_pubkey(left)
        right_key = format_openssh_pubkey(right)
        return [
            '{!r} == {!r}'.format(left, right),
            '   {} != {}'.format(left_key, right_key)
        ]


used_port = 0


@yield_fixture
def fx_sftpd(request, tmpdir):
    global used_port
    getopt = request.config.getoption
    port_min = max(used_port + 1, getopt('--sshd-port-min'))
    port_max = min(port_min + 2, getopt('--sshd-port-max'))
    used_port = port_max
    servers = {}
    for port in range(port_min, port_max + 1):
        path = tmpdir.mkdir(str(port))
        terminated = threading.Event()
        thread = threading.Thread(
            target=start_server,
            args=(str(path), '127.0.0.1', port, terminated)
        )
        servers[port] = thread, path, terminated
    yield servers
    for port, (th, _, ev) in servers.items():
        ev.set()
    for port, (th, _, ev) in servers.items():
        if th.is_alive():
            th.join(10)
        assert not th.is_alive(), '{!r} (for port #{}) is still alive'.format(
            th, port
        )


@fixture
def fx_authorized_keys():
    return [RSAKey.generate(1024) for _ in range(5)]


@yield_fixture
def fx_authorized_sftp(fx_sftpd, fx_authorized_keys):
    port, (thread, path, ev) = fx_sftpd.popitem()
    thread.start()
    key = RSAKey.generate(1024)
    dot_ssh = path.mkdir('.ssh')
    with dot_ssh.join('authorized_keys').open('w') as f:
        print(format_openssh_pubkey(key), file=f)
        for authorized_key in fx_authorized_keys:
            print(format_openssh_pubkey(authorized_key), file=f)
    transport = Transport(('127.0.0.1', port))
    transport.connect(username='user', pkey=key)
    sftp_client = SFTPClient.from_transport(transport)
    yield sftp_client, path, [key] + fx_authorized_keys
    sftp_client.close()
    transport.close()


@fixture
def fx_master_key():
    return RSAKey.generate(1024)


@fixture
def fx_authorized_servers(fx_sftpd, fx_master_key):
    for port, (thread, path, ev) in fx_sftpd.items():
        with path.mkdir('.ssh').join('authorized_keys').open('w') as f:
            f.write(format_openssh_pubkey(fx_master_key))
        thread.start()
    return fx_sftpd
