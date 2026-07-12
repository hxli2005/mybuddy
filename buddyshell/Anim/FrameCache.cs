using System.IO;
using System.Windows.Media.Imaging;

namespace BuddyShell.Anim;

public sealed class FrameCache
{
    private readonly int _capacity;
    private readonly long _maxDecodedBytes;
    private readonly Dictionary<string, Entry> _entries = new(StringComparer.OrdinalIgnoreCase);
    private long _accessSequence;
    private long _estimatedBytes;

    public FrameCache(int capacity = 64, long maxDecodedBytes = 64L * 1024 * 1024)
    {
        _capacity = Math.Max(16, capacity);
        _maxDecodedBytes = Math.Max(16L * 1024 * 1024, maxDecodedBytes);
    }

    public int Count => _entries.Count;
    public long EstimatedBytes => _estimatedBytes;

    public BitmapImage Get(string path)
    {
        var fullPath = Path.GetFullPath(path);
        var info = new FileInfo(fullPath);
        if (!info.Exists) throw new FileNotFoundException("动画帧不存在。", fullPath);

        var stamp = (info.LastWriteTimeUtc.Ticks, info.Length);
        if (_entries.TryGetValue(fullPath, out var cached) && cached.Stamp == stamp)
        {
            cached.LastAccess = ++_accessSequence;
            return cached.Bitmap;
        }

        var bitmap = new BitmapImage();
        bitmap.BeginInit();
        bitmap.CacheOption = BitmapCacheOption.OnLoad;
        bitmap.CreateOptions = BitmapCreateOptions.PreservePixelFormat;
        bitmap.UriSource = new Uri(fullPath, UriKind.Absolute);
        bitmap.EndInit();
        bitmap.Freeze();

        if (_entries.Remove(fullPath, out var stale)) _estimatedBytes -= stale.EstimatedBytes;
        var estimatedBytes = Math.Max(1L, (long)bitmap.PixelWidth * bitmap.PixelHeight * 4);
        _entries[fullPath] = new Entry(bitmap, stamp, ++_accessSequence, estimatedBytes);
        _estimatedBytes += estimatedBytes;
        Trim();
        return bitmap;
    }

    public void Clear()
    {
        _entries.Clear();
        _estimatedBytes = 0;
    }

    private void Trim()
    {
        while (_entries.Count > 1 && (_entries.Count > _capacity || _estimatedBytes > _maxDecodedBytes))
        {
            var oldest = _entries.MinBy(pair => pair.Value.LastAccess);
            if (_entries.Remove(oldest.Key, out var removed)) _estimatedBytes -= removed.EstimatedBytes;
        }
    }

    private sealed class Entry(
        BitmapImage bitmap,
        (long LastWriteTicks, long Length) stamp,
        long lastAccess,
        long estimatedBytes)
    {
        public BitmapImage Bitmap { get; } = bitmap;
        public (long LastWriteTicks, long Length) Stamp { get; } = stamp;
        public long LastAccess { get; set; } = lastAccess;
        public long EstimatedBytes { get; } = estimatedBytes;
    }
}
