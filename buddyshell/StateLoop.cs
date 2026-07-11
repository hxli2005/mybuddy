using BuddyShell.Bridge;
using System.Windows.Threading;

namespace BuddyShell;

public sealed class StateLoop : IDisposable
{
    private readonly BridgeClient _client;
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromSeconds(20) };
    private readonly CancellationTokenSource _stop = new();
    private bool _polling;

    public StateLoop(BridgeClient client)
    {
        _client = client;
        _timer.Tick += async (_, _) => await PollAsync();
    }

    public event EventHandler<VPetStateResponse>? Updated;
    public event EventHandler<Exception>? Failed;

    public void Start()
    {
        _timer.Start();
        _ = PollAsync();
    }

    public async Task PollAsync()
    {
        if (_polling) return;
        _polling = true;
        try
        {
            var state = await _client.GetStateAsync(_stop.Token);
            Updated?.Invoke(this, state);
        }
        catch (OperationCanceledException) when (_stop.IsCancellationRequested)
        {
            return;
        }
        catch (Exception exception)
        {
            Failed?.Invoke(this, exception);
        }
        finally
        {
            _polling = false;
        }
    }

    public void Dispose()
    {
        _timer.Stop();
        _stop.Cancel();
        _stop.Dispose();
    }
}
