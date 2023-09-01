"""Test Redis URL parser"""
from asyncio import PriorityQueue
from urllib.parse import urlparse

from _socket import TCP_KEEPCNT, TCP_KEEPINTVL
from django.test import TestCase

from authentik.lib.utils.parser import get_redis_options, process_config


# pylint: disable=too-many-public-methods
class TestParserUtils(TestCase):
    """Test Redis URL parser"""

    def test_process_config(self):
        """Test Redis URL parser for direct Redis connection"""
        url = urlparse("redis://myredis:5678/34")
        config = process_config(url, *get_redis_options(url))
        self.assertEqual(config["type"], "default")
        self.assertEqual(config["redis_kwargs"]["host"], "myredis")
        self.assertEqual(config["redis_kwargs"]["port"], 5678)
        self.assertEqual(config["redis_kwargs"]["db"], 34)

    def test_process_config_sentinel(self):
        """Test Redis URL parser for Sentinel connection"""
        url = urlparse("redis+sentinel://mysentinel:22345/92?mastername=themaster")
        config = process_config(url, *get_redis_options(url))
        self.assertEqual(config["type"], "sentinel")
        self.assertEqual(config["sentinels"][0]["host"], "mysentinel")
        self.assertEqual(config["sentinels"][0]["port"], 22345)
        self.assertEqual(config["sentinels"][0]["db"], 92)

    def test_process_config_cluster(self):
        """Test Redis URL parser for cluster connection"""
        url = urlparse("redis+cluster://mycluster:8652/393")
        config = process_config(url, *get_redis_options(url))
        self.assertEqual(config["type"], "cluster")
        self.assertEqual(config["addrs"][0][0], "mycluster")
        self.assertEqual(config["addrs"][0][1], 8652)
        self.assertTrue("db" not in config["redis_kwargs"])

    def test_get_redis_options_addr_arg(self):
        """Test Redis URL parser with addr arg"""
        url = urlparse("redis://myredis:6379/0?addr=newmyredis")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["addrs"], [("newmyredis", 6379), ("myredis", 6379)])

    def test_get_redis_options_addrs_arg(self):
        """Test Redis URL parser with addrs arg"""
        url = urlparse("redis://myredis:6379/0?addrs=newmyredis:1234,otherredis")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(
            redis_kwargs["addrs"], [("newmyredis", 1234), ("otherredis", 6379), ("myredis", 6379)]
        )

    def test_get_redis_options_redis_credentials(self):
        """Test Redis URL parser with basic auth credentials"""
        url = urlparse("redis://redis:password@myredis/0")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["username"], "redis")
        self.assertEqual(redis_kwargs["password"], "password")

    def test_get_redis_options_redis_username_arg(self):
        """Test Redis URL parser with username arg"""
        url = urlparse("redis://redis:password@myredis/0?username=newredis")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["username"], "newredis")

    def test_get_redis_options_only_redis_username(self):
        """Test Redis URL parser with only username in basic auth"""
        url = urlparse("redis://redis:@myredis/0")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["username"], "redis")

    def test_get_redis_options_redis_password_arg(self):
        """Test Redis URL parser with password arg"""
        url = urlparse("redis://redis:password@myredis/0?password=%22%27%25+%21.%3B.%C2%B0")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["password"], "\"'% !.;.°")

    def test_get_redis_options_only_redis_password(self):
        """Test Redis URL parser with only password in basic auth"""
        url = urlparse("redis://password@myredis/0")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["password"], "password")

    def test_get_redis_options_database_arg(self):
        """Test Redis URL parser with password arg"""
        url = urlparse("redis://password@myredis?database=15")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["db"], 15)

    def test_get_redis_options_db_arg(self):
        """Test Redis URL parser with password arg"""
        url = urlparse("redis://password@myredis?db=10")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["db"], 10)

    def test_get_redis_options_max_retries_arg(self):
        """Test Redis URL parser with maxretries arg"""
        url = urlparse("redis://myredis:6379/0?maxretries=123")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["cluster_error_retry_attempts"], 123)

    def test_get_redis_options_min_retry_backoff_arg(self):
        """Test Redis URL parser with minretrybackoff arg"""
        url = urlparse("redis://myredis/0?minretrybackoff=100s")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["retry"]["min_backoff"], 100)

    def test_get_redis_options_max_retry_backoff_arg(self):
        """Test Redis URL parser with maxretrybackoff arg"""
        url = urlparse("redis://myredis/0?maxretrybackoff=100s")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["retry"]["max_backoff"], 100)

    def test_get_redis_options_timeout_arg(self):
        """Test Redis URL parser with timeout arg"""
        url = urlparse("redis://myredis/0?timeout=100s")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["socket_timeout"], 100)
        self.assertEqual(redis_kwargs["socket_connect_timeout"], 100)

    def test_get_redis_options_timeout_arg_empty(self):
        """Test Redis URL parser with empty timeout arg"""
        url = urlparse("redis://myredis/0?timeout=")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["socket_timeout"], 3)
        self.assertEqual(redis_kwargs["socket_connect_timeout"], 5)

    def test_get_redis_options_timeout_arg_invalid_format(self):
        """Test Redis URL parser with invalid format timeout arg"""
        url = urlparse("redis://myredis/0?timeout=39l")
        with self.assertRaises(ValueError):
            _, _, _ = get_redis_options(url)

    def test_get_redis_options_timeout_arg_invalid_char(self):
        """Test Redis URL parser with invalid char timeout arg"""
        url = urlparse("redis://myredis/0?timeout=39,34h")
        with self.assertRaises(ValueError):
            _, _, _ = get_redis_options(url)

    def test_get_redis_options_timeout_arg_missing_unit(self):
        """Test Redis URL parser with missing unit timeout arg"""
        url = urlparse("redis://myredis/0?timeout=43")
        with self.assertRaises(ValueError):
            _, _, _ = get_redis_options(url)

    def test_get_redis_options_timeout_arg_milliseconds(self):
        """Test Redis URL parser with millisecond timeout arg"""
        url = urlparse("redis://myredis/0?timeout=10000ms")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["socket_timeout"], 10)
        self.assertEqual(redis_kwargs["socket_connect_timeout"], 10)

    def test_get_redis_options_dial_timeout_arg(self):
        """Test Redis URL parser with dialtimeout arg"""
        url = urlparse("redis://myredis/0?dialtimeout=100s")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["socket_connect_timeout"], 100)

    def test_get_redis_options_read_timeout_arg(self):
        """Test Redis URL parser with readtimeout arg"""
        url = urlparse("redis://myredis/0?readtimeout=100s")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["socket_timeout"], 100)

    def test_get_redis_options_write_timeout_arg(self):
        """Test Redis URL parser with writetimeout arg"""
        url = urlparse("redis://myredis/0?writetimeout=100s")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["socket_timeout"], 100)

    def test_get_redis_options_pool_fifo_arg(self):
        """Test Redis URL parser with poolfifo arg"""
        url = urlparse("redis://myredis/0?poolfifo=true")
        pool_kwargs, _, _ = get_redis_options(url)
        self.assertEqual(pool_kwargs["queue_class"], PriorityQueue)

    def test_get_redis_options_pool_fifo_arg_fallback(self):
        """Test Redis URL parser with bad poolfifo arg

        No queue class shall be set in this case and
        instead the default asyncio.LifoQueue of the
        BlockingConnectionPool is used
        """
        url = urlparse("redis://myredis/0?poolfifo=abc")
        pool_kwargs, _, _ = get_redis_options(url)
        self.assertTrue("queue_class" not in pool_kwargs)

    def test_get_redis_options_pool_size_arg(self):
        """Test Redis URL parser with poolsize arg"""
        url = urlparse("redis://myredis/0?poolsize=32")
        pool_kwargs, _, _ = get_redis_options(url)
        self.assertEqual(pool_kwargs["max_connections"], 32)

    def test_get_redis_options_pool_timeout_arg(self):
        """Test Redis URL parser with pooltimeout arg"""
        url = urlparse("redis://myredis/0?pooltimeout=100s")
        pool_kwargs, _, _ = get_redis_options(url)
        self.assertEqual(pool_kwargs["timeout"], 100)

    def test_get_redis_options_idle_timeout_arg(self):
        """Test Redis URL parser with idletimeout arg"""
        url = urlparse("redis://myredis/0?idletimeout=100s")
        config = process_config(url, *get_redis_options(url))
        self.assertEqual(config["redis_kwargs"]["socket_keepalive_options"][TCP_KEEPCNT], 100 // 60)

    def test_get_redis_options_idle_timeout_arg_socket(self):
        """Test Redis URL parser with idletimeout arg for Redis socket connection"""
        url = urlparse("redis+socket://myredis/0?idletimeout=100s")
        config = process_config(url, *get_redis_options(url))
        self.assertFalse("socket_keepalive_options" in config["redis_kwargs"])

    # TODO: This is not supported by the Go Redis URL parser!
    def test_get_redis_options_idle_check_frequency_arg(self):
        """Test Redis URL parser with idlecheckfrequency arg"""
        url = urlparse("redis://myredis/0?idlecheckfrequency=31s")
        config = process_config(url, *get_redis_options(url))
        self.assertEqual(config["redis_kwargs"]["socket_keepalive_options"][TCP_KEEPINTVL], 31)

    # TODO: This is not supported by the Go Redis URL parser!
    def test_get_redis_options_idle_check_frequency_arg_socket(self):
        """Test Redis URL parser with idlecheckfrequency arg for Redis socket connection"""
        url = urlparse("redis+socket://myredis/0?idlecheckfrequency=42s")
        config = process_config(url, *get_redis_options(url))
        self.assertFalse("socket_keepalive_options" in config["redis_kwargs"])

    def test_get_redis_options_max_idle_conns_arg(self):
        """Test Redis URL parser with maxidleconns arg"""
        url = urlparse("redis://myredis/0?maxidleconns=52")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["max_connections"], 52)

    def test_get_redis_options_sentinel_master_id_arg(self):
        """Test Redis URL parser with sentinelmasterid arg"""
        url = urlparse("redis://myredis/0?sentinelmasterid=themaster")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["service_name"], "themaster")

    def test_get_redis_options_master_name_arg(self):
        """Test Redis URL parser with mastername arg"""
        url = urlparse("redis://myredis/0?mastername=themaster")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["service_name"], "themaster")

    def test_get_redis_options_sentinel_credentials(self):
        """Test Redis URL parser with sentinelusername and sentinelpassword arg"""
        url = urlparse(
            "redis+sentinel://redis:password@myredis/0"
            + "?sentinelusername=suser&sentinelpassword=spass"
        )
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["sentinel_username"], "suser")
        self.assertEqual(redis_kwargs["sentinel_password"], "spass")

    def test_get_redis_options_readonly_arg(self):
        """Test Redis URL parser with readonly arg"""
        url = urlparse("redis://myredis/0?readonly=true")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertEqual(redis_kwargs["readonly"], True)

    def test_get_redis_options_skip_verify_arg(self):
        """Test Redis URL parser with skipverify arg"""
        url = urlparse("redis://myredis/0?skipverify=true")
        _, _, tls_kwargs = get_redis_options(url)
        self.assertEqual(tls_kwargs["ssl_cert_reqs"], "optional")

    def test_get_redis_options_insecure_skip_verify_arg(self):
        """Test Redis URL parser with insecureskipverify arg"""
        url = urlparse("redis://myredis/0?insecureskipverify=true")
        _, _, tls_kwargs = get_redis_options(url)
        self.assertEqual(tls_kwargs["ssl_cert_reqs"], "none")

    def test_get_redis_options_minidleconns_arg(self):
        """Test Redis URL parser with minidleconns arg"""
        url = urlparse("redis://myredis/0?minidleconns=4")
        _, redis_kwargs, _ = get_redis_options(url)
        self.assertFalse("minidleconns" in redis_kwargs)
        self.assertFalse("min_idle_conns" in redis_kwargs)

    def test_get_redis_options_unknown_arg(self):
        """Test Redis URL parser with an unknown arg"""
        url = urlparse("redis://myredis/0?notanarg=4")
        with self.assertRaises(ValueError):
            get_redis_options(url)
