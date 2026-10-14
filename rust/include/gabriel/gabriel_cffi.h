#define GABRIEL_ABI_VERSION 1
#define GABRIEL_MAX_FLOW_NAME 64
#define GABRIEL_MAX_PAYLOAD_TYPE_NAME 64
#define GABRIEL_MAX_ENGINE_NAME 64
#define GABRIEL_MAX_PRODUCER_NAME 64
enum gabriel_status_t
 {
  GABRIEL_STATUS_T_OK = 0,
  GABRIEL_STATUS_T_ERR_INVALID,
  GABRIEL_STATUS_T_ERR_SIZE_EXCEEDED,
  GABRIEL_STATUS_T_ERR_AGAIN,
  GABRIEL_STATUS_T_ERR_INTERNAL,
};
typedef uint32_t gabriel_status_t;
enum gabriel_event_kind_t
 {
  GABRIEL_EVENT_KIND_T_ACCEPTED,
  GABRIEL_EVENT_KIND_T_SENT,
  GABRIEL_EVENT_KIND_T_RESULT,
  GABRIEL_EVENT_KIND_T_SKIPPED,
  GABRIEL_EVENT_KIND_T_TIMED_OUT,
  GABRIEL_EVENT_KIND_T_DISCONNECTED,
  GABRIEL_EVENT_KIND_T_DERIVED,
  GABRIEL_EVENT_KIND_T_LOST,
};
typedef uint32_t gabriel_event_kind_t;
enum gabriel_forward_t
 {
  GABRIEL_FORWARD_T_STOP,
  GABRIEL_FORWARD_T_PASS,
  GABRIEL_FORWARD_T_EMIT,
};
typedef uint32_t gabriel_forward_t;
typedef struct gabriel_client_t gabriel_client_t;
typedef struct gabriel_client_options_t gabriel_client_options_t;
typedef struct gabriel_engine_t gabriel_engine_t;
typedef struct gabriel_producer_t gabriel_producer_t;
typedef struct gabriel_server_t gabriel_server_t;
typedef struct gabriel_server_options_t gabriel_server_options_t;
typedef struct gabriel_subscription_t gabriel_subscription_t;
typedef struct {
  const char *producer;
  uint64_t session;
  uint64_t seq;
} gabriel_input_id_t;
typedef struct {
  gabriel_input_id_t id;
  const char *payload_type;
  const uint8_t *buf;
  size_t len;
} gabriel_input_t;
typedef struct {
  gabriel_event_kind_t kind;
  uint64_t lost;
  gabriel_input_id_t id;
  const char *engine;
  const char *flow;
  gabriel_forward_t forward;
  const char *payload_type;
  const uint8_t *result;
  size_t result_len;
  uint64_t at_ns;
  uint64_t offered_at_ns;
  uint64_t received_at_ns;
} gabriel_event_t;
uint32_t gabriel_abi_version(void);
const char *gabriel_last_error(void);
gabriel_status_t gabriel_log_init(const char *filter);
gabriel_status_t gabriel_producer_create(const char *name,
                                         size_t max_input_size,
                                         gabriel_producer_t **producer);
gabriel_status_t gabriel_producer_destroy(gabriel_producer_t *producer);
gabriel_status_t gabriel_producer_new_input(gabriel_producer_t *producer, uint8_t **buf);
gabriel_status_t gabriel_producer_publish_input(gabriel_producer_t *producer,
                                                size_t len,
                                                const char *flow,
                                                uint64_t *seq);
gabriel_status_t gabriel_client_options_create(gabriel_client_options_t **options);
gabriel_status_t gabriel_client_options_destroy(gabriel_client_options_t *options);
gabriel_status_t gabriel_client_options_set_remote_server(gabriel_client_options_t *options,
                                                          const char *addr,
                                                          const uint8_t *certificate,
                                                          size_t certificate_len);
gabriel_status_t gabriel_client_options_set_remote_crossing_capacity(gabriel_client_options_t *options,
                                                                     size_t capacity);
gabriel_status_t gabriel_client_create(const char *name,
                                       const char *local_server,
                                       const gabriel_client_options_t *options,
                                       gabriel_client_t **client);
gabriel_status_t gabriel_client_destroy(gabriel_client_t *client);
gabriel_status_t gabriel_client_add_producer(gabriel_client_t *client, const char *name);
gabriel_status_t gabriel_client_remove_producer(gabriel_client_t *client, const char *name);
gabriel_status_t gabriel_client_add_flow(gabriel_client_t *client,
                                         const char *name,
                                         const char *spec);
gabriel_status_t gabriel_client_remove_flow(gabriel_client_t *client, const char *name);
gabriel_status_t gabriel_server_options_create(gabriel_server_options_t **options);
gabriel_status_t gabriel_server_options_destroy(gabriel_server_options_t *options);
gabriel_status_t gabriel_server_options_set_bind_addr(gabriel_server_options_t *options,
                                                      const char *addr);
gabriel_status_t gabriel_server_options_set_identity_dir(gabriel_server_options_t *options,
                                                         const char *dir);
gabriel_status_t gabriel_server_create(const char *name,
                                       const gabriel_server_options_t *options,
                                       gabriel_server_t **server);
gabriel_status_t gabriel_server_destroy(gabriel_server_t *server);
gabriel_status_t gabriel_server_remote_port(gabriel_server_t *server, uint16_t *port);
gabriel_status_t gabriel_server_certificate(gabriel_server_t *server,
                                            const uint8_t **certificate,
                                            size_t *len);
gabriel_status_t gabriel_server_add_engine(gabriel_server_t *server, const char *name);
gabriel_status_t gabriel_server_remove_engine(gabriel_server_t *server, const char *name);
gabriel_status_t gabriel_engine_create(const char *name,
                                       const char *result_type,
                                       size_t max_output_size,
                                       gabriel_engine_t **engine);
gabriel_status_t gabriel_engine_destroy(gabriel_engine_t *engine);
gabriel_status_t gabriel_engine_wait(gabriel_engine_t *engine, uint32_t timeout_ms);
gabriel_status_t gabriel_engine_poll_input(gabriel_engine_t *engine, gabriel_input_t *input);
gabriel_status_t gabriel_engine_subscribe(gabriel_engine_t *engine, const char *name);
gabriel_status_t gabriel_engine_poll_event(gabriel_engine_t *engine, gabriel_event_t *event);
gabriel_status_t gabriel_engine_publish_derived(gabriel_engine_t *engine,
                                                const gabriel_input_id_t *id,
                                                const char *flow,
                                                const char *result_type,
                                                const uint8_t *result,
                                                size_t result_len);
gabriel_status_t gabriel_engine_stop(gabriel_engine_t *engine,
                                     const uint8_t *result,
                                     size_t result_len);
gabriel_status_t gabriel_engine_pass(gabriel_engine_t *engine,
                                     const uint8_t *result,
                                     size_t result_len);
gabriel_status_t gabriel_engine_new_output(gabriel_engine_t *engine, uint8_t **buf);
gabriel_status_t gabriel_engine_emit(gabriel_engine_t *engine,
                                     const char *output_type,
                                     size_t output_len,
                                     const uint8_t *result,
                                     size_t result_len);
gabriel_status_t gabriel_subscribe_producer(const char *name,
                                            gabriel_subscription_t **subscription);
gabriel_status_t gabriel_subscribe_engine(const char *name, gabriel_subscription_t **subscription);
gabriel_status_t gabriel_subscription_destroy(gabriel_subscription_t *subscription);
gabriel_status_t gabriel_subscription_wait(gabriel_subscription_t *subscription,
                                           uint32_t timeout_ms);
gabriel_status_t gabriel_subscription_poll(gabriel_subscription_t *subscription,
                                           gabriel_event_t *event);
