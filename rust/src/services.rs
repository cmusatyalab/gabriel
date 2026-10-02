//! iceoryx2 services that connect Gabriel's local components. Every side of a
//! service opens it through here, so they always agree on its types.

use iceoryx2::prelude::*;
use iceoryx2::service::port_factory::{event, publish_subscribe};

use crate::envelope::Envelope;
use crate::{naming, Error, Result};

/// Payload of a producer's input service.
pub(crate) type InputPayload = [u8];

/// Opens (or creates) the input service of the producer `name`.
pub(crate) fn open_producer_input(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<publish_subscribe::PortFactory<ipc::Service, InputPayload, Envelope>> {
    node.service_builder(&naming::producer_input_service(name)?)
        .publish_subscribe::<InputPayload>()
        .user_header::<Envelope>()
        .open_or_create()
        .map_err(|e| Error::Internal(format!("failed to open input of producer {name:?}: {e}")))
}

/// Opens (or creates) the event the producer `name` signals after each input.
pub(crate) fn open_producer_ready(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<event::PortFactory<ipc::Service>> {
    node.service_builder(&naming::producer_ready_service(name)?)
        .event()
        .open_or_create()
        .map_err(|e| {
            Error::Internal(format!(
                "failed to open ready event of producer {name:?}: {e}"
            ))
        })
}
