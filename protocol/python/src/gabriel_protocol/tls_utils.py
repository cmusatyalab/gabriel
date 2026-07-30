"""Helpers for optionally configuring gRPC transport security.

Shared by both the server and client packages so that TLS/mTLS configuration
(loading PEM files into grpc.ChannelCredentials/grpc.ServerCredentials) is done
consistently on both ends of a connection.
"""

from typing import Optional

import grpc


def _read(path: Optional[str]) -> Optional[bytes]:
    if path is None:
        return None
    with open(path, "rb") as f:
        return f.read()


def build_server_credentials(
    cert_path: Optional[str],
    key_path: Optional[str],
    ca_cert_path: Optional[str] = None,
) -> Optional[grpc.ServerCredentials]:
    """Build server-side credentials from PEM files, or None for insecure.

    Args:
        cert_path: Path to the server's PEM certificate chain.
        key_path: Path to the server's PEM private key.
        ca_cert_path:
            Optional path to a PEM CA certificate used to verify client
            certificates. Providing this enables mutual TLS, requiring
            clients to present a certificate signed by this CA.

    Returns:
        A grpc.ServerCredentials, or None if cert_path/key_path are not
        both provided (the caller should fall back to an insecure port).
    """
    if not cert_path or not key_path:
        return None

    return grpc.ssl_server_credentials(
        [(_read(key_path), _read(cert_path))],
        root_certificates=_read(ca_cert_path),
        require_client_auth=ca_cert_path is not None,
    )


def build_channel_credentials(
    ca_cert_path: Optional[str] = None,
    cert_path: Optional[str] = None,
    key_path: Optional[str] = None,
) -> Optional[grpc.ChannelCredentials]:
    """Build client-side channel credentials from PEM files.

    Args:
        ca_cert_path:
            Optional path to a PEM CA certificate used to verify the
            server's certificate. If omitted, the system's default trust
            store is used.
        cert_path:
            Optional path to a PEM client certificate, presented to the
            server for mutual TLS. Must be given together with key_path.
        key_path:
            Optional path to a PEM client private key, presented to the
            server for mutual TLS. Must be given together with cert_path.

    Returns:
        A grpc.ChannelCredentials, or None if none of the arguments were
        provided (the caller should fall back to an insecure channel).
    """
    if not ca_cert_path and not cert_path and not key_path:
        return None

    if bool(cert_path) != bool(key_path):
        raise ValueError("cert_path and key_path must be provided together")

    return grpc.ssl_channel_credentials(
        root_certificates=_read(ca_cert_path),
        private_key=_read(key_path),
        certificate_chain=_read(cert_path),
    )
