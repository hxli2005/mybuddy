using BuddyShell.Bridge;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;

namespace BuddyShell;

public sealed class EngineHost : IDisposable
{
    private readonly Process _process;

    private EngineHost(Process process) => _process = process;

    public static EngineHost? Start(ShellSettings settings)
    {
        var executable = Path.Combine(AppContext.BaseDirectory, "engine", "MyBuddyEngine.exe");
        if (!File.Exists(executable))
        {
            App.LogMessage("event=engine_external reason=bundled_engine_not_found");
            return null;
        }
        if (!PortIsAvailable(8000))
            throw new InvalidOperationException(
                "8000 端口已被其他程序占用，小布的心智桥无法启动。");

        var mindDirectory = Path.Combine(SettingsStore.DataDirectory, "mind");
        Directory.CreateDirectory(mindDirectory);
        var start = new ProcessStartInfo(executable)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            WorkingDirectory = AppContext.BaseDirectory,
        };
        start.ArgumentList.Add("web");
        start.ArgumentList.Add("--config");
        start.ArgumentList.Add(Path.Combine(AppContext.BaseDirectory, "config.default.yaml"));
        start.ArgumentList.Add("--data-dir");
        start.ArgumentList.Add(mindDirectory);
        start.ArgumentList.Add("--port");
        start.ArgumentList.Add("8000");
        start.ArgumentList.Add("--parent-pid");
        start.ArgumentList.Add(Environment.ProcessId.ToString());
        start.Environment["MYBUDDY_API_KEY"] = SettingsStore.ReadApiKey(settings);

        var process = new Process { StartInfo = start, EnableRaisingEvents = true };
        process.OutputDataReceived += (_, args) => LogEngineLine("stdout", args.Data);
        process.ErrorDataReceived += (_, args) => LogEngineLine("stderr", args.Data);
        process.Exited += (_, _) => App.LogMessage($"event=engine_exit code={process.ExitCode}");
        if (!process.Start())
            throw new InvalidOperationException("小布的心智桥启动失败。");
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        return new EngineHost(process);
    }

    private static bool PortIsAvailable(int port)
    {
        var listener = new TcpListener(IPAddress.Loopback, port);
        try
        {
            listener.Start();
            return true;
        }
        catch (SocketException)
        {
            return false;
        }
        finally
        {
            listener.Stop();
        }
    }

    private static void LogEngineLine(string stream, string? line)
    {
        if (!string.IsNullOrWhiteSpace(line))
            App.LogMessage($"event=engine_{stream} {line}");
    }

    public void Dispose() => _process.Dispose();
}
