package edu.cmu.cs.gabriel.client

import gabriel_protocol.v1.Gabriel

/**
 * A named source of [Gabriel.InputFrame]s sent to a fixed set of cognitive
 * engines. Gabriel gives each producer its own pool of tokens, so producers
 * can be throttled independently of one another; see the "Details" section
 * of the top-level Gabriel README for the full token/producer model.
 *
 * @param name a unique identifier for this producer, e.g. "openrtist" or
 *   "face". Sent to the server as `producer_id`.
 * @param targetEngineIds the cognitive engines this producer's frames should
 *   be routed to.
 * @param produce called to generate the next frame whenever a token is
 *   available for this producer. Must not block; suspend instead.
 */
class InputProducer(
    val name: String,
    val targetEngineIds: List<String>,
    private val produce: suspend () -> Gabriel.InputFrame,
) {
    init {
        require(name.isNotEmpty()) { "InputProducer name must not be empty" }
        require(targetEngineIds.isNotEmpty()) {
            "InputProducer \"$name\" has no target engines"
        }
    }

    suspend fun nextFrame(): Gabriel.InputFrame = produce()
}
