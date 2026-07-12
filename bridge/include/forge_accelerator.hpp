#ifndef FORGE_ACCELERATOR_HPP
#define FORGE_ACCELERATOR_HPP

#include "forge_accelerator.h"

#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace forge::accelerator {

class Error final : public std::runtime_error {
public:
    Error(forge_accel_status status, int http_status, std::string body)
        : std::runtime_error("Forge accelerator request failed"),
          status(status), http_status(http_status), body(std::move(body)) {}

    forge_accel_status status;
    int http_status;
    std::string body;
};

struct Response {
    int status;
    std::string body;
    std::string request_id;
};

class Client final {
public:
    Client(std::string token, forge_accel_transport_fn transport, void *context = nullptr)
        : token_(std::move(token)) {
        const auto status = forge_accel_client_init(&client_, token_.c_str(), transport, context);
        if (status != FORGE_ACCEL_OK) throw std::invalid_argument("invalid Forge client settings");
    }

    Client(const Client &) = delete;
    Client &operator=(const Client &) = delete;
    Client(Client &&) = delete;
    Client &operator=(Client &&) = delete;

    [[nodiscard]] Response capabilities() const {
        return invoke([&](forge_accel_response *response) {
            return forge_accel_capabilities(&client_, response);
        });
    }

    [[nodiscard]] Response verify_scratch(std::string_view json) const {
        return invoke_json(forge_accel_verify_scratch_json, json);
    }

    [[nodiscard]] Response compile_coreml(std::string_view json) const {
        return invoke_json(forge_accel_compile_coreml_json, json);
    }

    [[nodiscard]] Response predict_coreml(std::string_view json) const {
        return invoke_json(forge_accel_predict_coreml_json, json);
    }

    [[nodiscard]] Response compile_metal(std::string_view json) const {
        return invoke_json(forge_accel_compile_metal_json, json);
    }

    [[nodiscard]] Response dispatch_metal(std::string_view json) const {
        return invoke_json(forge_accel_dispatch_metal_json, json);
    }

    [[nodiscard]] Response job(std::string_view id) const {
        const std::string owned(id);
        return invoke([&](forge_accel_response *response) {
            return forge_accel_job(&client_, owned.c_str(), response);
        });
    }

    [[nodiscard]] Response cancel(std::string_view id) const {
        const std::string owned(id);
        return invoke([&](forge_accel_response *response) {
            return forge_accel_cancel_job(&client_, owned.c_str(), response);
        });
    }

    [[nodiscard]] Response release_coreml(std::string_view id) const {
        const std::string owned(id);
        return invoke([&](forge_accel_response *response) {
            return forge_accel_release_coreml(&client_, owned.c_str(), response);
        });
    }

    [[nodiscard]] Response release_metal(std::string_view id) const {
        const std::string owned(id);
        return invoke([&](forge_accel_response *response) {
            return forge_accel_release_metal(&client_, owned.c_str(), response);
        });
    }

private:
    template <typename Function>
    [[nodiscard]] Response invoke(Function function) const {
        std::vector<char> storage(1024 * 1024);
        forge_accel_response response{0, storage.data(), storage.size(), 0, {0}};
        const auto status = function(&response);
        const std::string body(response.body, response.body_length);
        if (status != FORGE_ACCEL_OK) throw Error(status, response.http_status, body);
        return {response.http_status, body, response.request_id};
    }

    template <typename Function>
    [[nodiscard]] Response invoke_json(Function function, std::string_view json) const {
        const std::string owned(json);
        return invoke([&](forge_accel_response *response) {
            return function(&client_, owned.c_str(), response);
        });
    }

    std::string token_;
    forge_accel_client client_{};
};

} // namespace forge::accelerator

#endif
