#ifndef FORGE_ACCELERATOR_H
#define FORGE_ACCELERATOR_H

#include <stddef.h>
#include <stdio.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FORGE_ACCEL_PROTOCOL_VERSION "1.0"
#define FORGE_ACCEL_DEFAULT_ENDPOINT "http://10.0.2.2:4777/accelerator/v1"
#define FORGE_ACCEL_SHA256_HEX_LENGTH 64u

typedef enum forge_accel_status {
    FORGE_ACCEL_OK = 0,
    FORGE_ACCEL_INVALID_ARGUMENT = 1,
    FORGE_ACCEL_TRANSPORT_ERROR = 2,
    FORGE_ACCEL_BUFFER_TOO_SMALL = 3,
    FORGE_ACCEL_HTTP_ERROR = 4
} forge_accel_status;

typedef enum forge_accel_compute_units {
    FORGE_ACCEL_COMPUTE_CPU,
    FORGE_ACCEL_COMPUTE_CPU_GPU,
    FORGE_ACCEL_COMPUTE_CPU_ANE,
    FORGE_ACCEL_COMPUTE_ALL
} forge_accel_compute_units;

typedef struct forge_accel_scratch_reference {
    const char *relative_path;
    const char *sha256;
    unsigned long long size;
    const char *media_type;
    int delete_after_read;
} forge_accel_scratch_reference;

typedef struct forge_accel_response {
    int http_status;
    char *body;
    size_t body_capacity;
    size_t body_length;
    char request_id[37];
} forge_accel_response;

/*
 * Implement this callback with the guest's HTTP stack. It must refuse
 * redirects, send Authorization: Bearer <token>, and send the supplied
 * X-Forge-Protocol-Version. It writes a complete JSON object into response.
 */
typedef forge_accel_status (*forge_accel_transport_fn)(
    void *context,
    const char *method,
    const char *url,
    const char *bearer_token,
    const char *protocol_version,
    const char *json_body,
    forge_accel_response *response
);

typedef struct forge_accel_client {
    const char *endpoint;
    const char *bearer_token;
    forge_accel_transport_fn transport;
    void *transport_context;
} forge_accel_client;

static inline int forge_accel_is_lower_hex(char character) {
    return (character >= '0' && character <= '9') ||
           (character >= 'a' && character <= 'f');
}

static inline int forge_accel_valid_uuid(const char *value) {
    size_t index;
    if (value == NULL || strlen(value) != 36u) return 0;
    for (index = 0u; index < 36u; ++index) {
        if (index == 8u || index == 13u || index == 18u || index == 23u) {
            if (value[index] != '-') return 0;
        } else if (!forge_accel_is_lower_hex(value[index]) &&
                   !(value[index] >= 'A' && value[index] <= 'F')) {
            return 0;
        }
    }
    return 1;
}

static inline int forge_accel_valid_scratch_reference(
    const forge_accel_scratch_reference *reference
) {
    size_t index;
    if (reference == NULL || reference->relative_path == NULL ||
        reference->sha256 == NULL || reference->relative_path[0] == '/' ||
        reference->relative_path[0] == '\0' ||
        strstr(reference->relative_path, "//") != NULL ||
        strstr(reference->relative_path, "/../") != NULL ||
        strstr(reference->relative_path, "/./") != NULL ||
        strncmp(reference->relative_path, "../", 3u) == 0 ||
        strncmp(reference->relative_path, "./", 2u) == 0 ||
        (strlen(reference->relative_path) >= 3u &&
         strcmp(reference->relative_path + strlen(reference->relative_path) - 3u, "/..") == 0) ||
        (strlen(reference->relative_path) >= 2u &&
         strcmp(reference->relative_path + strlen(reference->relative_path) - 2u, "/.") == 0) ||
        strcmp(reference->relative_path, "..") == 0 ||
        strcmp(reference->relative_path, ".") == 0 ||
        strlen(reference->sha256) != FORGE_ACCEL_SHA256_HEX_LENGTH) return 0;
    for (index = 0u; index < FORGE_ACCEL_SHA256_HEX_LENGTH; ++index) {
        if (!forge_accel_is_lower_hex(reference->sha256[index])) return 0;
    }
    return 1;
}

static inline forge_accel_status forge_accel_client_init(
    forge_accel_client *client,
    const char *bearer_token,
    forge_accel_transport_fn transport,
    void *transport_context
) {
    if (client == NULL || bearer_token == NULL || strlen(bearer_token) < 32u ||
        strchr(bearer_token, ' ') != NULL || strchr(bearer_token, '\t') != NULL ||
        strchr(bearer_token, '\n') != NULL || transport == NULL) {
        return FORGE_ACCEL_INVALID_ARGUMENT;
    }
    client->endpoint = FORGE_ACCEL_DEFAULT_ENDPOINT;
    client->bearer_token = bearer_token;
    client->transport = transport;
    client->transport_context = transport_context;
    return FORGE_ACCEL_OK;
}

static inline forge_accel_status forge_accel_request_json(
    const forge_accel_client *client,
    const char *method,
    const char *path,
    const char *json_body,
    forge_accel_response *response
) {
    char url[1536];
    int written;
    forge_accel_status status;
    if (client == NULL || client->transport == NULL || method == NULL ||
        path == NULL || path[0] != '/' || strstr(path, "..") != NULL ||
        response == NULL || response->body == NULL || response->body_capacity == 0u) {
        return FORGE_ACCEL_INVALID_ARGUMENT;
    }
    written = snprintf(url, sizeof(url), "%s%s", client->endpoint, path);
    if (written < 0 || (size_t)written >= sizeof(url)) return FORGE_ACCEL_INVALID_ARGUMENT;
    response->body_length = 0u;
    response->body[0] = '\0';
    response->request_id[0] = '\0';
    status = client->transport(
        client->transport_context,
        method,
        url,
        client->bearer_token,
        FORGE_ACCEL_PROTOCOL_VERSION,
        json_body,
        response
    );
    if (status != FORGE_ACCEL_OK) return status;
    if (response->body_length >= response->body_capacity) return FORGE_ACCEL_BUFFER_TOO_SMALL;
    response->body[response->body_length] = '\0';
    return response->http_status >= 200 && response->http_status < 300
        ? FORGE_ACCEL_OK : FORGE_ACCEL_HTTP_ERROR;
}

static inline forge_accel_status forge_accel_capabilities(
    const forge_accel_client *client,
    forge_accel_response *response
) {
    return forge_accel_request_json(client, "GET", "/capabilities", NULL, response);
}

static inline forge_accel_status forge_accel_verify_scratch_json(
    const forge_accel_client *client,
    const char *request_json,
    forge_accel_response *response
) {
    if (request_json == NULL) return FORGE_ACCEL_INVALID_ARGUMENT;
    return forge_accel_request_json(client, "POST", "/scratch/verify", request_json, response);
}

static inline forge_accel_status forge_accel_compile_coreml_json(
    const forge_accel_client *client,
    const char *request_json,
    forge_accel_response *response
) {
    if (request_json == NULL) return FORGE_ACCEL_INVALID_ARGUMENT;
    return forge_accel_request_json(client, "POST", "/coreml/compilations", request_json, response);
}

static inline forge_accel_status forge_accel_predict_coreml_json(
    const forge_accel_client *client,
    const char *request_json,
    forge_accel_response *response
) {
    if (request_json == NULL) return FORGE_ACCEL_INVALID_ARGUMENT;
    return forge_accel_request_json(client, "POST", "/coreml/predictions", request_json, response);
}

static inline forge_accel_status forge_accel_compile_metal_json(
    const forge_accel_client *client,
    const char *request_json,
    forge_accel_response *response
) {
    if (request_json == NULL) return FORGE_ACCEL_INVALID_ARGUMENT;
    return forge_accel_request_json(client, "POST", "/metal/libraries", request_json, response);
}

static inline forge_accel_status forge_accel_dispatch_metal_json(
    const forge_accel_client *client,
    const char *request_json,
    forge_accel_response *response
) {
    if (request_json == NULL) return FORGE_ACCEL_INVALID_ARGUMENT;
    return forge_accel_request_json(client, "POST", "/metal/dispatches", request_json, response);
}

static inline forge_accel_status forge_accel_job(
    const forge_accel_client *client,
    const char *job_id,
    forge_accel_response *response
) {
    char path[64];
    if (!forge_accel_valid_uuid(job_id)) return FORGE_ACCEL_INVALID_ARGUMENT;
    (void)snprintf(path, sizeof(path), "/jobs/%s", job_id);
    return forge_accel_request_json(client, "GET", path, NULL, response);
}

static inline forge_accel_status forge_accel_release_coreml(
    const forge_accel_client *client,
    const char *model_id,
    forge_accel_response *response
) {
    char path[80];
    if (!forge_accel_valid_uuid(model_id)) return FORGE_ACCEL_INVALID_ARGUMENT;
    (void)snprintf(path, sizeof(path), "/coreml/models/%s", model_id);
    return forge_accel_request_json(client, "DELETE", path, NULL, response);
}

static inline forge_accel_status forge_accel_release_metal(
    const forge_accel_client *client,
    const char *library_id,
    forge_accel_response *response
) {
    char path[84];
    if (!forge_accel_valid_uuid(library_id)) return FORGE_ACCEL_INVALID_ARGUMENT;
    (void)snprintf(path, sizeof(path), "/metal/libraries/%s", library_id);
    return forge_accel_request_json(client, "DELETE", path, NULL, response);
}

static inline forge_accel_status forge_accel_job_events(
    const forge_accel_client *client,
    const char *job_id,
    unsigned long long after,
    unsigned int wait_seconds,
    forge_accel_response *response
) {
    char path[128];
    if (!forge_accel_valid_uuid(job_id) || wait_seconds > 30u) {
        return FORGE_ACCEL_INVALID_ARGUMENT;
    }
    (void)snprintf(
        path,
        sizeof(path),
        "/jobs/%s/events?after=%llu&wait_seconds=%u",
        job_id,
        after,
        wait_seconds
    );
    return forge_accel_request_json(client, "GET", path, NULL, response);
}

static inline forge_accel_status forge_accel_cancel_job(
    const forge_accel_client *client,
    const char *job_id,
    forge_accel_response *response
) {
    char path[64];
    if (!forge_accel_valid_uuid(job_id)) return FORGE_ACCEL_INVALID_ARGUMENT;
    (void)snprintf(path, sizeof(path), "/jobs/%s", job_id);
    return forge_accel_request_json(client, "DELETE", path, NULL, response);
}

#ifdef __cplusplus
}
#endif

#endif
