//! Control calls from a client to a server. Unlike inputs and events, they have no fixed layout,
//! so they are encoded with postcard. The same messages will go over QUIC to a remote server.

use serde::{Deserialize, Serialize, de::DeserializeOwned};

use crate::{Error, Result, routing::server_router::InstallFlow};

/// The version of these messages. A server refuses a call made with another version, so a client
/// and a server built from different versions never misread each other.
pub(crate) const PROTOCOL_VERSION: u32 = 1;

/// A control call, with the version of the protocol it was made with.
#[derive(Debug, PartialEq, Serialize, Deserialize)]
pub(crate) struct Request {
    pub(crate) version: u32,
    pub(crate) call: Call,
}

impl Request {
    /// The call `call`, in this version of the protocol.
    pub(crate) fn new(call: Call) -> Self {
        Self {
            version: PROTOCOL_VERSION,
            call,
        }
    }
}

#[derive(Debug, PartialEq, Serialize, Deserialize)]
pub(crate) enum Call {
    /// Install the client's part of a flow. The server sends the flow's events to `client`.
    Install { client: String, spec: InstallFlow },
    /// Remove the flow `session`, which `client` installed.
    RemoveFlow { client: String, session: u64 },
}

#[derive(Debug, PartialEq, Serialize, Deserialize)]
pub(crate) enum Reply {
    /// The flow is installed. The client names `session` in every input it sends to the flow.
    Installed {
        session: u64,
    },
    Removed,
}

/// What a server answers to a call.
pub(crate) type Response = std::result::Result<Reply, Error>;

pub(crate) fn encode<T: Serialize>(message: &T) -> Result<Vec<u8>> {
    postcard::to_stdvec(message)
        .map_err(|e| Error::Internal(format!("failed to encode a control message: {e}")))
}

pub(crate) fn decode<T: DeserializeOwned>(bytes: &[u8]) -> Result<T> {
    postcard::from_bytes(bytes)
        .map_err(|e| Error::InvalidArgument(format!("failed to decode a control message: {e}")))
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;
    use crate::routing::{
        ids::{EdgeId, NodeId},
        server_router::{CrossingSpec, EdgeSpec},
    };

    #[test]
    fn install_survives_encoding() {
        let request = Request::new(Call::Install {
            client: String::from("client"),
            spec: InstallFlow {
                flow: String::from("tracking"),
                engines: vec![(NodeId(0), String::from("detector"))],
                edges: vec![],
                crossings: vec![CrossingSpec {
                    id: EdgeId(2),
                    targets: vec![EdgeSpec {
                        id: EdgeId(1),
                        from: NodeId(1),
                        to: NodeId(0),
                        timeout: Duration::from_millis(250),
                    }],
                }],
            },
        });

        let decoded: Request = decode(&encode(&request).unwrap()).unwrap();

        assert_eq!(decoded, request);
    }

    #[test]
    fn error_survives_encoding() {
        let response: Response = Err(Error::InvalidArgument(String::from("no such engine")));

        let decoded: Response = decode(&encode(&response).unwrap()).unwrap();

        assert!(
            matches!(decoded, Err(Error::InvalidArgument(message)) if message == "no such engine")
        );
    }

    #[test]
    fn garbage_is_rejected() {
        let result = decode::<Request>(&[0xff; 3]);

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }
}
