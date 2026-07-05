import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'alerts.dart';

/// One moving reflector reported by the station.
class SensorTarget {
  const SensorTarget({
    required this.range,
    required this.az,
    required this.vel,
    required this.snr,
  });

  final double range; // metres from the station
  final double az; // degrees, right positive
  final double vel; // m/s
  final double snr;
}

/// A motion episode recorded by the station (one per burst of movement).
class SensorEvent {
  const SensorEvent({
    required this.id,
    required this.wall,
    required this.range,
    required this.az,
    required this.snr,
  });

  final int id;
  final DateTime wall;
  final double range;
  final double az;
  final double snr;
}

/// Single source of truth for the Python sonar station (sensor.py).
///
/// Polls the live frame (`/`) for the radar, the event log (`/events`) for
/// the activity feed + notifications, and pushes settings back via `/config`.
class SensorService extends ChangeNotifier {
  SensorService();

  /// Same machine -> localhost. From a phone, set the laptop's LAN IP in the
  /// Station tab, e.g. '192.168.1.42:8765' (the sensor binds 0.0.0.0).
  String host = 'localhost:8765';

  /// User-facing label for the station shown in the Station tab.
  String deviceName = 'MacBook Pro · Room A4';

  /// Mirrors the station's armed state: arming/disarming here starts/stops the
  /// actual chirp on the laptop (sensor.py /config?armed=).
  bool armed = true;
  bool pushAlerts = true;

  bool live = false;
  double fov = 80;
  double maxRange = 2.0;
  List<SensorTarget> targets = const [];
  List<SensorEvent> events = const [];

  /// Called once per fresh motion event while armed — drives the in-app
  /// banner (independent of the OS notification, which "Push alerts" gates).
  void Function(SensorEvent e)? onMotionEvent;

  static const _notifyCooldown = Duration(seconds: 12);

  Timer? _frameTimer;
  Timer? _eventTimer;
  int _lastNotifiedId = -1; // -1 = baseline not set (skip pre-existing events)
  DateTime _lastNotifiedAt = DateTime.fromMillisecondsSinceEpoch(0);
  // After a local toggle, ignore the frame's armed flag until the config
  // request has certainly landed (stops one in-flight stale frame bouncing
  // the switch back).
  DateTime _armedPendingUntil = DateTime.fromMillisecondsSinceEpoch(0);

  void start() {
    if (_frameTimer != null) return;
    _frameTimer = Timer.periodic(
        const Duration(milliseconds: 150), (_) => _pollFrame());
    _eventTimer =
        Timer.periodic(const Duration(seconds: 1), (_) => _pollEvents());
  }

  void stop() {
    _frameTimer?.cancel();
    _eventTimer?.cancel();
    _frameTimer = null;
    _eventTimer = null;
    if (live || targets.isNotEmpty) {
      live = false;
      targets = const [];
      notifyListeners();
    }
  }

  void setArmed(bool v) {
    armed = v;
    _armedPendingUntil = DateTime.now().add(const Duration(seconds: 1));
    notifyListeners();
    _pushConfig('armed=${v ? 1 : 0}');
  }

  void setPushAlerts(bool v) {
    pushAlerts = v;
    notifyListeners();
  }

  /// Push the trusted azimuth cone half-angle (degrees) to the station.
  Future<void> setFov(double deg) => _pushConfig('fov=${deg.round()}');

  void setDeviceName(String v) {
    final n = v.trim();
    if (n.isEmpty || n == deviceName) return;
    deviceName = n;
    notifyListeners();
  }

  void setHost(String v) {
    final h = v.trim();
    if (h.isEmpty || h == host) return;
    host = h;
    live = false;
    targets = const [];
    events = const [];
    _lastNotifiedId = -1; // re-baseline against the new station
    notifyListeners();
  }

  Future<void> _pushConfig(String query) async {
    try {
      await http
          .get(Uri.parse('http://$host/config?$query'))
          .timeout(const Duration(seconds: 2));
    } catch (e) {
      debugPrint('config push failed ($query): $e');
    }
  }

  Future<void> _pollFrame() async {
    try {
      final r = await http
          .get(Uri.parse('http://$host/'))
          .timeout(const Duration(milliseconds: 400));
      if (r.statusCode != 200) throw 'status ${r.statusCode}';
      final j = jsonDecode(r.body) as Map<String, dynamic>;
      maxRange = (j['max_range'] as num?)?.toDouble() ?? maxRange;
      fov = (j['fov'] as num?)?.toDouble() ?? fov;
      if (DateTime.now().isAfter(_armedPendingUntil)) {
        armed = (j['armed'] as bool?) ?? armed;
      }
      targets = [
        for (final t in (j['targets'] as List? ?? const []))
          SensorTarget(
            range: ((t as Map)['range'] as num).toDouble(),
            az: (t['az'] as num?)?.toDouble() ?? 0,
            vel: (t['vel'] as num?)?.toDouble() ?? 0,
            snr: (t['snr'] as num?)?.toDouble() ?? 0,
          ),
      ];
      live = true;
    } catch (_) {
      live = false;
      targets = const [];
    }
    notifyListeners();
  }

  Future<void> _pollEvents() async {
    try {
      final r = await http
          .get(Uri.parse('http://$host/events'))
          .timeout(const Duration(milliseconds: 900));
      if (r.statusCode != 200) return;
      final j = jsonDecode(r.body) as Map<String, dynamic>;
      final latestId = (j['latest_id'] as num?)?.toInt() ?? 0;
      final parsed = [
        for (final e in (j['events'] as List? ?? const []))
          SensorEvent(
            id: ((e as Map)['id'] as num).toInt(),
            wall: DateTime.fromMillisecondsSinceEpoch(
                (((e['wall'] as num?)?.toDouble() ?? 0) * 1000).round()),
            range: (e['range'] as num?)?.toDouble() ?? 0,
            az: (e['az'] as num?)?.toDouble() ?? 0,
            snr: (e['snr'] as num?)?.toDouble() ?? 0,
          ),
      ]..sort((a, b) => b.id.compareTo(a.id)); // newest first
      events = parsed;

      if (_lastNotifiedId < 0) {
        // First contact: show history, but only alert on events from now on.
        _lastNotifiedId = latestId;
      } else if (latestId > _lastNotifiedId) {
        final fresh = parsed.where((e) => e.id > _lastNotifiedId).toList();
        _lastNotifiedId = latestId;
        if (armed && fresh.isNotEmpty) {
          onMotionEvent?.call(fresh.first);
          if (pushAlerts &&
              DateTime.now().difference(_lastNotifiedAt) > _notifyCooldown) {
            _lastNotifiedAt = DateTime.now();
            Alerts.motion(range: fresh.first.range);
          }
        }
      }
      notifyListeners();
    } catch (_) {
      // frame poll already tracks connectivity
    }
  }

  @override
  void dispose() {
    stop();
    super.dispose();
  }
}
