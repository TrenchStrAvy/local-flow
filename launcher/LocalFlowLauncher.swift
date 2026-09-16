import AppKit
import Darwin
import Foundation

private let serviceLabel = "com.localflow.dictation"

private struct CommandResult {
    let status: Int32
    let stdout: String
    let stderr: String
}

private struct ServiceInspection {
    let stateSeen: Bool
    let running: Bool
    let pid: Int32?
}

private struct LauncherConfiguration {
    let launchctlPath: String
    let uid: uid_t
    let homeDirectory: String
    let errorToStderr: Bool
    let pollInterval: TimeInterval
    let pollAttempts: Int

    static func current() -> LauncherConfiguration {
#if LOCALFLOW_TESTING
        let environment = ProcessInfo.processInfo.environment
        let configuredUID = environment["LOCALFLOW_TEST_UID"].flatMap(uid_t.init)
        let pollMilliseconds =
            environment["LOCALFLOW_POLL_INTERVAL_MS"].flatMap(Double.init) ?? 100
        let pollAttempts =
            environment["LOCALFLOW_POLL_ATTEMPTS"].flatMap(Int.init) ?? 50
        return LauncherConfiguration(
            launchctlPath: environment["LOCALFLOW_LAUNCHCTL_PATH"] ?? "/bin/launchctl",
            uid: configuredUID ?? getuid(),
            homeDirectory: environment["LOCALFLOW_TEST_HOME"] ?? NSHomeDirectory(),
            errorToStderr: environment["LOCALFLOW_ERROR_MODE"] == "stderr",
            pollInterval: pollMilliseconds / 1_000,
            pollAttempts: pollAttempts
        )
#else
        let currentUID = getuid()
        let home = getpwuid(currentUID).map {
            String(cString: $0.pointee.pw_dir)
        } ?? ""
        return LauncherConfiguration(
            launchctlPath: "/bin/launchctl",
            uid: currentUID,
            homeDirectory: home,
            errorToStderr: false,
            pollInterval: 0.1,
            pollAttempts: 50
        )
#endif
    }
}

private final class LaunchctlClient {
    private let executablePath: String

    init(executablePath: String) {
        self.executablePath = executablePath
    }

    func run(_ arguments: [String]) throws -> CommandResult {
        let process = Process()
        let output = Pipe()
        let error = Pipe()
        process.executableURL = URL(fileURLWithPath: executablePath)
        process.arguments = arguments
        process.standardOutput = output
        process.standardError = error
        try process.run()
        process.waitUntilExit()
        return CommandResult(
            status: process.terminationStatus,
            stdout: String(
                data: output.fileHandleForReading.readDataToEndOfFile(),
                encoding: .utf8
            ) ?? "",
            stderr: String(
                data: error.fileHandleForReading.readDataToEndOfFile(),
                encoding: .utf8
            ) ?? ""
        )
    }
}

private final class Launcher {
    private let configuration: LauncherConfiguration
    private let launchctl: LaunchctlClient
    private var lastInspectionOutput = ""

    init(configuration: LauncherConfiguration) {
        self.configuration = configuration
        launchctl = LaunchctlClient(executablePath: configuration.launchctlPath)
    }

    func run() -> Int32 {
        let target = "gui/\(configuration.uid)/\(serviceLabel)"
        do {
            let result = try launchctl.run(["print", target])
            if isServiceNotFound(result) {
                return try bootstrapAndStart(target)
            }
            guard result.status == 0 else {
                return fail(
                    "Could not inspect the LocalFlow service. "
                        + commandDiagnostic(result)
                )
            }
            let inspection = inspect(result.stdout)
            guard inspection.stateSeen else {
                return fail(
                    "launchctl returned a malformed service state; "
                        + "startup was not changed."
                )
            }
            if inspection.running {
                guard let pid = inspection.pid, pid > 0 else {
                    return fail(
                        "LocalFlow reports running without a valid PID; "
                            + "startup was not changed."
                    )
                }
                return 0
            }
            let kickstart = try launchctl.run(["kickstart", target])
            guard kickstart.status == 0 else {
                return fail("Could not start the LocalFlow service.")
            }
            return try pollUntilRunning(target)
                ? 0
                : fail(startupFailureMessage())
        } catch {
            return fail("Could not launch LocalFlow: \(error.localizedDescription)")
        }
    }

    private func isServiceNotFound(_ result: CommandResult) -> Bool {
        result.status == 113
            && result.stderr.contains("Could not find service")
            && result.stderr.contains("in domain for user gui:")
    }

    private func commandDiagnostic(_ result: CommandResult) -> String {
        let detail = result.stderr.isEmpty ? result.stdout : result.stderr
        return detail.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func bootstrapAndStart(_ target: String) throws -> Int32 {
        guard !configuration.homeDirectory.isEmpty else {
            return fail("Could not resolve your home directory.")
        }
        let domain = "gui/\(configuration.uid)"
        let plist = URL(fileURLWithPath: configuration.homeDirectory)
            .appendingPathComponent("Library")
            .appendingPathComponent("LaunchAgents")
            .appendingPathComponent("\(serviceLabel).plist")
            .path
        let bootstrap = try launchctl.run(["bootstrap", domain, plist])
        guard bootstrap.status == 0 else {
            return fail("Could not load the LocalFlow service.")
        }

        let inspectionResult = try launchctl.run(["print", target])
        if inspectionResult.status == 0 {
            let inspection = inspect(inspectionResult.stdout)
            guard inspection.stateSeen else {
                return fail("launchctl returned a malformed service state.")
            }
            if inspection.running, let pid = inspection.pid, pid > 0 {
                return 0
            }
        }

        let kickstart = try launchctl.run(["kickstart", target])
        guard kickstart.status == 0 else {
            return fail("Could not start the LocalFlow service.")
        }
        return try pollUntilRunning(target)
            ? 0
            : fail(startupFailureMessage())
    }

    private func pollUntilRunning(_ target: String) throws -> Bool {
        for attempt in 0..<configuration.pollAttempts {
            if attempt > 0 {
                Thread.sleep(forTimeInterval: configuration.pollInterval)
            }
            let result = try launchctl.run(["print", target])
            guard result.status == 0 else {
                return false
            }
            lastInspectionOutput = result.stdout
            let inspection = inspect(result.stdout)
            if inspection.running, let pid = inspection.pid, pid > 0 {
                return true
            }
        }
        return false
    }

    private func startupFailureMessage() -> String {
        let observed = lastInspectionOutput
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if observed.isEmpty {
            return "LocalFlow did not remain running after startup."
        }
        return "LocalFlow did not remain running after startup. Last state: \(observed)"
    }

    private func inspect(_ output: String) -> ServiceInspection {
        var stateSeen = false
        var running = false
        var pid: Int32?
        for line in output.split(separator: "\n") {
            let field = line.trimmingCharacters(in: .whitespaces)
            if field == "state = running" {
                stateSeen = true
                running = true
            } else if field == "state = not running" {
                stateSeen = true
            } else if field.hasPrefix("pid = ") {
                pid = Int32(field.dropFirst("pid = ".count))
            }
        }
        return ServiceInspection(stateSeen: stateSeen, running: running, pid: pid)
    }

    private func fail(_ detail: String) -> Int32 {
        let message = "\(detail) Check ~/Library/Logs/local-flow.log for details."
        if configuration.errorToStderr {
            FileHandle.standardError.write(Data((message + "\n").utf8))
        } else {
            let application = NSApplication.shared
            application.setActivationPolicy(.accessory)
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = "LocalFlow could not start"
            alert.informativeText = message
            alert.addButton(withTitle: "OK")
            alert.runModal()
        }
        return 1
    }
}

@main
private struct LocalFlowLauncherMain {
    static func main() {
        let status = Launcher(configuration: .current()).run()
        exit(status)
    }
}
