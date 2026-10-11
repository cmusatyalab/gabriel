//! Messages between a client and a server, encoded with postcard. Control calls have no fixed
//! layout, so they are encoded on both transports. Inputs and events have fixed-size records on
//! one machine; over QUIC, their records are encoded too, followed by their bytes as they are.

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

/// Decodes a call a client sent, and refuses one made with another version of the protocol.
pub(crate) fn decode_call(bytes: &[u8]) -> Result<Call> {
    let request: Request = decode(bytes)?;
    if request.version != PROTOCOL_VERSION {
        return Err(Error::InvalidArgument(format!(
            "protocol version {} is not {PROTOCOL_VERSION}",
            request.version
        )));
    }
    Ok(request.call)
}

pub(crate) fn encode<T: Serialize>(message: &T) -> Result<Vec<u8>> {
    postcard::to_stdvec(message)
        .map_err(|e| Error::Internal(format!("failed to encode a message: {e}")))
}

pub(crate) fn decode<T: DeserializeOwned>(bytes: &[u8]) -> Result<T> {
    postcard::from_bytes(bytes)
        .map_err(|e| Error::InvalidArgument(format!("failed to decode a message: {e}")))
}

/// Encodes `header`, followed by `payload` as it is. Inputs and events are sent this way, so their
/// bytes are not encoded one by one.
pub(crate) fn encode_with_payload<T: Serialize>(header: &T, payload: &[u8]) -> Result<Vec<u8>> {
    let mut bytes = encode(header)?;
    bytes.extend_from_slice(payload);
    Ok(bytes)
}

/// Decodes a header made by [`encode_with_payload`], and returns it with the payload after it.
pub(crate) fn decode_with_payload<T: DeserializeOwned>(bytes: &[u8]) -> Result<(T, &[u8])> {
    postcard::take_from_bytes(bytes)
        .map_err(|e| Error::InvalidArgument(format!("failed to decode a message: {e}")))
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;
    use crate::{
        clock::Timestamp,
        ipc::types::{EngineName, EventRecord, FlowName},
        routing::{
            ids::{EdgeId, InputId, NodeId, OfferId},
            server_router::{CrossingSpec, EdgeSpec},
        },
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
    fn event_and_its_result_survive_encoding() {
        let record = EventRecord::sent(
            OfferId::default(),
            InputId::default(),
            EngineName::try_from("detector").unwrap(),
            FlowName::try_from("tracking").unwrap(),
        )
        .offered_at(Timestamp::from_nanos(7));
        let result = [1, 2, 3];

        let bytes = encode_with_payload(&record, &result).unwrap();
        let (decoded, payload) = decode_with_payload::<EventRecord>(&bytes).unwrap();

        assert_eq!(decoded, record);
        assert_eq!(payload, result);
    }

    #[test]
    fn garbage_is_rejected() {
        let result = decode::<Request>(&[0xff; 3]);

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }
}
