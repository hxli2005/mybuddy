using System.Windows;
using System.Windows.Threading;

namespace BuddyShell.Anim;

public sealed class AnimationController : IAnimationController, IAnimationDiagnostics
{
    private readonly AnimationManifest _manifest;
    private readonly IAnimationRenderer _renderer;
    private readonly IAnimationClock _clock;
    private readonly DispatcherTimer? _timer;
    private AnimationPlan _desiredBaseline;
    private AnimationPlan? _plan;
    private AnimationPhasePlan? _phase;
    private AnimationExecutionKind? _execution;
    private AnimationRequest? _request;
    private AnimationRequest? _pendingThink;
    private long _phaseStartedAt;
    private long _generation;
    private string? _lastSignature;
    private bool _thinkBodyEntered;
    private bool _faulted;
    private bool _disposed;

    public AnimationController(
        AnimationManifest manifest,
        IAnimationRenderer renderer,
        IAnimationClock? clock = null,
        bool autoStart = true)
    {
        _manifest = manifest;
        _renderer = renderer;
        _clock = clock ?? new SystemAnimationClock();
        _desiredBaseline = manifest.Get("idle.default.normal");
        _renderer.TouchStarted += OnTouchStarted;
        _renderer.TouchDetected += OnTouchDetected;
        StartBaseline();
        if (autoStart)
        {
            _timer = new DispatcherTimer(DispatcherPriority.Render)
            {
                Interval = TimeSpan.FromMilliseconds(16),
            };
            _timer.Tick += (_, _) => Tick();
            _timer.Start();
        }
    }

    public UIElement View => _renderer.View;
    public event EventHandler<TouchDetectedEventArgs>? TouchDetected;
    public event EventHandler<AnimationFaultEventArgs>? Faulted;
    public event EventHandler<ActivityFinishedEventArgs>? ActivityFinished;

    public AnimationSnapshot Snapshot => new(
        _generation,
        _plan?.Id,
        _phase?.Kind,
        _execution,
        _request?.CorrelationId,
        _desiredBaseline.Id,
        _pendingThink is not null,
        _phase?.FrameCount ?? 0,
        _phase is not null);

    public string HostName => $"{nameof(AnimationController)}/{(_renderer as IAnimationDiagnostics)?.HostName ?? _renderer.GetType().Name}";
    public string AssetRoot => _manifest.PetRoot;
    public int CurrentFrameCount => Snapshot.CurrentFrameCount;
    public bool IsPlaying => Snapshot.IsPlaying;
    public TouchZone ClassifyTouch(double y, double height) =>
        (_renderer as IAnimationDiagnostics)?.ClassifyTouch(y, height)
        ?? (y <= height * 0.45 ? TouchZone.Head : TouchZone.Body);

    public void Submit(AnimationRequest request) => OnDispatcher(() =>
    {
        if (_disposed) return;
        if (request.Intent == AnimationIntent.Think)
        {
            if (request.Source != AnimationSource.Chat || _pendingThink is not null) return;
            _pendingThink = request;
            _thinkBodyEntered = false;
            if (_execution != AnimationExecutionKind.Transient) StartRequest(request);
            return;
        }
        StartRequest(request);
    });

    public void SetBaseline(AnimationIntent intent) => OnDispatcher(() =>
    {
        if (_disposed) return;
        var plan = _manifest.Resolve(new AnimationRequest(
            intent, AnimationSource.System, $"baseline:{intent}", AnimationPriority.Activity));
        if (!plan.IsBaseline) throw new InvalidOperationException($"动画不是 baseline：{intent}");
        _desiredBaseline = plan;
        if (_execution == AnimationExecutionKind.Baseline) StartBaseline();
    });

    public void BeginInteractive(AnimationRequest request) => OnDispatcher(() =>
    {
        if (_disposed || request.Source != AnimationSource.DirectManipulation) return;
        StartRequest(request, AnimationExecutionKind.Interactive);
    });

    public void EndInteractive(string correlationId) => OnDispatcher(() =>
    {
        if (_disposed || _execution != AnimationExecutionKind.Interactive ||
            _request?.CorrelationId != correlationId || _phase?.Kind == AnimationPhaseKind.Exit)
            return;
        if (_plan?.Exit is { } exit) StartPhase(exit);
        else ResumePersistentView();
    });

    public void Complete(string correlationId, AnimationOutcome outcome) => OnDispatcher(() =>
    {
        if (_disposed || _pendingThink?.CorrelationId != correlationId) return;
        _pendingThink = null;
        _thinkBodyEntered = false;
        if (outcome.Reaction is { } reaction && reaction != AnimationIntent.Think)
        {
            StartRequest(new AnimationRequest(
                reaction,
                AnimationSource.System,
                $"reply:{correlationId}",
                AnimationPriority.Response));
        }
        else if (_request?.CorrelationId == correlationId)
        {
            StartBaseline();
        }
    });

    public void Tick()
    {
        if (_disposed || _plan is null || _phase is null) return;
        var elapsed = Math.Max(0, _clock.ElapsedMilliseconds - _phaseStartedAt);
        var frame = AnimationTimeline.Compose(_generation, _plan.Id, _phase, elapsed);
        try
        {
            if (frame.Signature != _lastSignature)
            {
                _renderer.Render(frame);
                _lastSignature = frame.Signature;
            }
            if (_faulted)
            {
                _faulted = false;
                Faulted?.Invoke(this, new AnimationFaultEventArgs(null, true));
            }
        }
        catch (Exception exception)
        {
            var failedActivity = _execution == AnimationExecutionKind.Transient &&
                _request?.Source == AnimationSource.State
                ? _request.CorrelationId
                : null;
            if (!_faulted) Faulted?.Invoke(this, new AnimationFaultEventArgs(exception, false));
            _faulted = true;
            if (failedActivity is not null)
            {
                ResetToBaselineWithoutRender();
                ActivityFinished?.Invoke(
                    this, new ActivityFinishedEventArgs(failedActivity, "failed", "animation_fault"));
            }
            else if (_execution == AnimationExecutionKind.Interactive)
            {
                ResetToBaselineWithoutRender();
            }
            return;
        }
        if (AnimationTimeline.IsComplete(_phase, elapsed)) AdvancePhase();
    }

    private void StartRequest(AnimationRequest request, AnimationExecutionKind? execution = null)
    {
        if (_execution == AnimationExecutionKind.Transient &&
            _request?.Source == AnimationSource.State &&
            _request.CorrelationId != request.CorrelationId)
            ActivityFinished?.Invoke(
                this, new ActivityFinishedEventArgs(
                    _request.CorrelationId,
                    "interrupted",
                    InterruptionReason(request.Source)));

        _plan = _manifest.Resolve(request);
        _request = request;
        _execution = execution ?? (request.Intent == AnimationIntent.Think
            ? AnimationExecutionKind.Pending
            : AnimationExecutionKind.Transient);
        StartPhase(_plan.Entry ?? _plan.Body);
    }

    private void StartBaseline()
    {
        _plan = _desiredBaseline;
        _request = null;
        _execution = AnimationExecutionKind.Baseline;
        StartPhase(_plan.Entry ?? _plan.Body);
    }

    private void ResetToBaselineWithoutRender()
    {
        _plan = _desiredBaseline;
        _request = null;
        _execution = AnimationExecutionKind.Baseline;
        _phase = _plan.Entry ?? _plan.Body;
        _phaseStartedAt = _clock.ElapsedMilliseconds;
        _generation += 1;
        _lastSignature = null;
    }

    private static string InterruptionReason(AnimationSource source) => source switch
    {
        AnimationSource.Touch => "touch",
        AnimationSource.Chat => "chat",
        AnimationSource.DirectManipulation => "raise",
        _ => "activity_replaced",
    };

    private void StartPhase(AnimationPhasePlan phase)
    {
        _phase = phase;
        _phaseStartedAt = _clock.ElapsedMilliseconds;
        _generation += 1;
        _lastSignature = null;
        if (_execution == AnimationExecutionKind.Pending && phase.Kind == AnimationPhaseKind.Body)
            _thinkBodyEntered = true;
        Tick();
    }

    private void AdvancePhase()
    {
        if (_plan is null || _phase is null) return;
        if (_phase.Kind == AnimationPhaseKind.Entry)
        {
            StartPhase(_plan.Body);
            return;
        }
        if (_phase.Kind == AnimationPhaseKind.Body && _plan.Exit is { } exit)
        {
            StartPhase(exit);
            return;
        }
        ResumePersistentView();
    }

    private void ResumePersistentView()
    {
        var completedActivity = _execution == AnimationExecutionKind.Transient &&
            _request?.Source == AnimationSource.State
            ? _request.CorrelationId
            : null;
        if (_pendingThink is { } pending)
        {
            _plan = _manifest.Resolve(pending);
            _request = pending;
            _execution = AnimationExecutionKind.Pending;
            StartPhase(_thinkBodyEntered ? _plan.Body : _plan.Entry ?? _plan.Body);
        }
        else
        {
            StartBaseline();
        }
        if (completedActivity is not null)
            ActivityFinished?.Invoke(
                this, new ActivityFinishedEventArgs(completedActivity, "completed", "animation_finished"));
    }

    private void OnTouchStarted(object? sender, TouchDetectedEventArgs args) => Submit(
        new AnimationRequest(
            args.Zone == TouchZone.Head ? AnimationIntent.TouchHeadReflex : AnimationIntent.TouchBodyReflex,
            AnimationSource.Touch,
            args.CorrelationId,
            AnimationPriority.Touch));

    private void OnTouchDetected(object? sender, TouchDetectedEventArgs args) =>
        TouchDetected?.Invoke(this, args);

    private void OnDispatcher(Action action)
    {
        if (_renderer.View.Dispatcher.CheckAccess()) action();
        else _renderer.View.Dispatcher.Invoke(action);
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        _timer?.Stop();
        _renderer.TouchStarted -= OnTouchStarted;
        _renderer.TouchDetected -= OnTouchDetected;
        _renderer.Dispose();
    }
}
