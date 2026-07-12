#include "forge_accelerator.h"

#include <stdio.h>
#include <string.h>

/* Replace with a libcurl, curl-cffi, or guest-agent transport. */
static forge_accel_status transport(
    void *context,
    const char *method,
    const char *url,
    const char *token,
    const char *version,
    const char *body,
    forge_accel_response *response
) {
    (void)context;
    (void)token;
    (void)body;
    printf("%s %s (protocol %s)\n", method, url, version);
    response->http_status = 200;
    response->body_length = (size_t)snprintf(
        response->body,
        response->body_capacity,
        "{\"example\":true}"
    );
    return FORGE_ACCEL_OK;
}

int main(void) {
    char body[4096];
    forge_accel_client client;
    forge_accel_response response = {0, body, sizeof(body), 0, {0}};
    if (forge_accel_client_init(
            &client,
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            transport,
            NULL
        ) != FORGE_ACCEL_OK) return 1;
    return forge_accel_capabilities(&client, &response) == FORGE_ACCEL_OK ? 0 : 1;
}
