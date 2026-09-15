#include <arpa/inet.h>
#include <csignal>
#include <fcntl.h>
#include <netdb.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {

long long now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

struct ChildProcess {
    pid_t pid = -1;

    bool running() const {
        if (pid <= 0) {
            return false;
        }
        int status = 0;
        pid_t result = waitpid(pid, &status, WNOHANG);
        return result == 0;
    }

    void stop(int grace_ms = 1200) {
        if (pid <= 0) {
            return;
        }
        kill(pid, SIGTERM);
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(grace_ms);
        int status = 0;
        while (std::chrono::steady_clock::now() < deadline) {
            pid_t result = waitpid(pid, &status, WNOHANG);
            if (result == pid || result == -1) {
                pid = -1;
                return;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        kill(pid, SIGKILL);
        waitpid(pid, &status, 0);
        pid = -1;
    }
};

std::string json_escape(const std::string& value) {
    std::ostringstream out;
    for (char ch : value) {
        switch (ch) {
            case '\\': out << "\\\\"; break;
            case '"': out << "\\\""; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default: out << ch; break;
        }
    }
    return out.str();
}

void emit_event(const std::string& type, const std::string& message) {
    std::cout << "{\"event\":\"" << json_escape(type) << "\",\"message\":\"" << json_escape(message) << "\"}" << std::endl;
}

std::optional<std::string> json_value(const std::string& line, const std::string& key) {
    const std::string needle = "\"" + key + "\"";
    size_t pos = line.find(needle);
    if (pos == std::string::npos) {
        return std::nullopt;
    }
    pos = line.find(':', pos + needle.size());
    if (pos == std::string::npos) {
        return std::nullopt;
    }
    pos = line.find('"', pos + 1);
    if (pos == std::string::npos) {
        return std::nullopt;
    }
    std::string result;
    bool escaped = false;
    for (size_t i = pos + 1; i < line.size(); ++i) {
        char ch = line[i];
        if (escaped) {
            result.push_back(ch);
            escaped = false;
            continue;
        }
        if (ch == '\\') {
            escaped = true;
            continue;
        }
        if (ch == '"') {
            return result;
        }
        result.push_back(ch);
    }
    return std::nullopt;
}

std::string value_or(const std::optional<std::string>& value, const std::string& fallback) {
    return value && !value->empty() ? *value : fallback;
}

std::string host_from_rtsp(const std::string& url) {
    const std::string marker = "://";
    size_t start = url.find(marker);
    start = start == std::string::npos ? 0 : start + marker.size();
    size_t end = url.find('/', start);
    std::string hostport = url.substr(start, end == std::string::npos ? std::string::npos : end - start);
    size_t at = hostport.rfind('@');
    if (at != std::string::npos) {
        hostport = hostport.substr(at + 1);
    }
    size_t colon = hostport.rfind(':');
    return colon == std::string::npos ? hostport : hostport.substr(0, colon);
}

int port_from_rtsp(const std::string& url, int fallback = 554) {
    const std::string marker = "://";
    size_t start = url.find(marker);
    start = start == std::string::npos ? 0 : start + marker.size();
    size_t slash = url.find('/', start);
    std::string hostport = url.substr(start, slash == std::string::npos ? std::string::npos : slash - start);
    size_t colon = hostport.rfind(':');
    if (colon == std::string::npos) {
        return fallback;
    }
    try {
        return std::stoi(hostport.substr(colon + 1));
    } catch (...) {
        return fallback;
    }
}

bool tcp_connectable(const std::string& host, int port, int timeout_ms = 400) {
    addrinfo hints{};
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_family = AF_UNSPEC;
    addrinfo* info = nullptr;
    std::string port_text = std::to_string(port);
    if (getaddrinfo(host.c_str(), port_text.c_str(), &hints, &info) != 0) {
        return false;
    }

    bool ok = false;
    for (addrinfo* it = info; it != nullptr && !ok; it = it->ai_next) {
        int fd = socket(it->ai_family, it->ai_socktype, it->ai_protocol);
        if (fd < 0) {
            continue;
        }
        int flags = fcntl(fd, F_GETFL, 0);
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
        int result = connect(fd, it->ai_addr, it->ai_addrlen);
        if (result == 0) {
            ok = true;
        } else if (errno == EINPROGRESS) {
            pollfd pfd{fd, POLLOUT, 0};
            if (poll(&pfd, 1, timeout_ms) > 0) {
                int err = 0;
                socklen_t len = sizeof(err);
                getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len);
                ok = err == 0;
            }
        }
        close(fd);
    }
    freeaddrinfo(info);
    return ok;
}

ChildProcess spawn_process(const std::vector<std::string>& args) {
    ChildProcess child;
    pid_t pid = fork();
    if (pid < 0) {
        return child;
    }
    if (pid == 0) {
        int nullfd = open("/dev/null", O_RDWR);
        if (nullfd >= 0) {
            dup2(nullfd, STDOUT_FILENO);
            dup2(nullfd, STDERR_FILENO);
            close(nullfd);
        }
        std::vector<char*> argv;
        argv.reserve(args.size() + 1);
        for (const auto& arg : args) {
            argv.push_back(const_cast<char*>(arg.c_str()));
        }
        argv.push_back(nullptr);
        execvp(argv[0], argv.data());
        _exit(127);
    }
    child.pid = pid;
    return child;
}

std::string mediamtx_config(int port) {
    std::string path = "/tmp/mt11-video-core-mediamtx-" + std::to_string(getpid()) + ".yml";
    std::ofstream out(path);
    out << "rtspAddress: :" << port << "\n";
    out << "rtspTransports: [tcp]\n";
    out << "rtmp: no\nhls: no\nwebrtc: no\nsrt: no\nmoq: no\n";
    out << "paths:\n  all_others:\n    source: publisher\n";
    return path;
}

class VideoCore {
public:
    ~VideoCore() {
        stop_record();
        stop_relay();
    }

    void handle(const std::string& line) {
        std::string cmd = value_or(json_value(line, "cmd"), "");
        if (cmd == "ping") {
            emit_event("pong", "ok");
        } else if (cmd == "start_relay") {
            start_relay(line);
        } else if (cmd == "stop_relay") {
            stop_relay();
            emit_event("relay", "stopped");
        } else if (cmd == "start_record") {
            start_record(line);
        } else if (cmd == "stop_record") {
            stop_record();
            emit_event("record", "stopped");
        } else if (cmd == "quit") {
            running_ = false;
        } else {
            emit_event("error", "unknown command: " + cmd);
        }
    }

    void tick() {
        if (relay_enabled_) {
            if (relay_.pid <= 0) {
                if (now_ms() >= relay_next_retry_ms_) {
                    launch_relay();
                }
            } else if (!relay_.running()) {
                relay_.pid = -1;
                schedule_relay_retry("relay process exited");
            }
        }
        if (record_.pid > 0 && !record_.running()) {
            record_.pid = -1;
            emit_event("record", "process exited");
        }
    }

    bool running() const { return running_; }

private:
    struct RelayConfig {
        std::string source;
        std::string output;
        std::string mode;
        std::string ffmpeg;
        std::string mediamtx;
    };

    ChildProcess mediamtx_;
    ChildProcess relay_;
    ChildProcess record_;
    std::string mediamtx_config_path_;
    bool running_ = true;
    bool relay_enabled_ = false;
    RelayConfig relay_config_;
    int relay_attempts_ = 0;
    long long relay_next_retry_ms_ = 0;

    void start_relay(const std::string& line) {
        stop_relay();
        relay_config_.source = value_or(json_value(line, "source"), "");
        relay_config_.output = value_or(json_value(line, "output"), "rtsp://127.0.0.1:8554/cam2");
        relay_config_.mode = value_or(json_value(line, "mode"), "qgc_safe");
        relay_config_.ffmpeg = value_or(json_value(line, "ffmpeg"), "ffmpeg");
        relay_config_.mediamtx = value_or(json_value(line, "mediamtx"), "mediamtx");
        relay_enabled_ = true;
        relay_attempts_ = 0;
        relay_next_retry_ms_ = 0;
        launch_relay();
    }

    void launch_relay() {
        if (!relay_enabled_) {
            return;
        }
        if (relay_config_.source.empty()) {
            emit_event("relay_error", "source is empty");
            return;
        }
        if (!tcp_connectable(host_from_rtsp(relay_config_.source), port_from_rtsp(relay_config_.source))) {
            schedule_relay_retry("source not reachable");
            return;
        }
        int output_port = port_from_rtsp(relay_config_.output, 8554);
        if (!tcp_connectable("127.0.0.1", output_port)) {
            mediamtx_config_path_ = mediamtx_config(output_port);
            mediamtx_ = spawn_process({relay_config_.mediamtx, mediamtx_config_path_});
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
        }

        std::vector<std::string> args = {
            relay_config_.ffmpeg, "-hide_banner", "-loglevel", "warning",
            "-rtsp_transport", "udp", "-flags", "low_delay",
            "-fflags", "+discardcorrupt+genpts", "-analyzeduration", "500000",
            "-probesize", "500000", "-i", relay_config_.source, "-map", "0:v:0", "-an"
        };
        if (relay_config_.mode == "raw" || relay_config_.mode == "copy" || relay_config_.mode == "raw low latency") {
            args.insert(args.end(), {"-c:v", "copy"});
        } else {
            args.insert(args.end(), {
                "-vf", "fps=25,format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                "-tune", "zerolatency", "-profile:v", "baseline", "-level:v", "4.0",
                "-bf", "0", "-g", "25", "-keyint_min", "25", "-sc_threshold", "0",
                "-b:v", "2500k", "-maxrate", "3000k", "-bufsize", "1000k"
            });
        }
        args.insert(args.end(), {"-f", "rtsp", "-rtsp_transport", "tcp", "-muxdelay", "0", "-muxpreload", "0", relay_config_.output});
        relay_ = spawn_process(args);
        relay_attempts_ = 0;
        emit_event("relay", "started");
    }

    void stop_relay() {
        relay_enabled_ = false;
        relay_next_retry_ms_ = 0;
        relay_.stop();
        mediamtx_.stop();
        if (!mediamtx_config_path_.empty()) {
            unlink(mediamtx_config_path_.c_str());
            mediamtx_config_path_.clear();
        }
    }

    void schedule_relay_retry(const std::string& reason) {
        relay_.stop();
        ++relay_attempts_;
        int exponential_ms = 500 * (1 << std::min(relay_attempts_, 7));
        int delay_ms = std::min(60000, std::max(15000, exponential_ms));
        relay_next_retry_ms_ = now_ms() + delay_ms;
        emit_event("relay_wait", reason + ", retry in " + std::to_string(delay_ms / 1000) + "s");
    }

    void start_record(const std::string& line) {
        stop_record();
        std::string url = value_or(json_value(line, "url"), "");
        std::string output = value_or(json_value(line, "output"), "");
        std::string transport = value_or(json_value(line, "transport"), "tcp");
        std::string ffmpeg = value_or(json_value(line, "ffmpeg"), "ffmpeg");
        if (url.empty() || output.empty()) {
            emit_event("record_error", "missing url or output");
            return;
        }
        record_ = spawn_process({
            ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
            "-rtsp_transport", transport, "-i", url, "-map", "0:v:0",
            "-an", "-c:v", "copy", output
        });
        emit_event("record", "started");
    }

    void stop_record() {
        record_.stop(4000);
    }
};

}  // namespace

int main() {
    std::ios::sync_with_stdio(false);
    emit_event("ready", "mt11_video_core");
    VideoCore core;
    std::string buffer;
    while (core.running()) {
        pollfd pfd{STDIN_FILENO, POLLIN, 0};
        int result = poll(&pfd, 1, 500);
        if (result > 0 && (pfd.revents & POLLIN)) {
            char chunk[1024];
            ssize_t count = read(STDIN_FILENO, chunk, sizeof(chunk));
            if (count <= 0) {
                break;
            }
            buffer.append(chunk, static_cast<size_t>(count));
            size_t newline = std::string::npos;
            while ((newline = buffer.find('\n')) != std::string::npos) {
                std::string line = buffer.substr(0, newline);
                buffer.erase(0, newline + 1);
                if (!line.empty() && line.back() == '\r') {
                    line.pop_back();
                }
                if (!line.empty()) {
                    core.handle(line);
                }
            }
        } else if (result > 0 && (pfd.revents & (POLLHUP | POLLERR | POLLNVAL))) {
            break;
        } else if (result < 0 && errno != EINTR) {
            break;
        }
        core.tick();
    }
    return 0;
}
