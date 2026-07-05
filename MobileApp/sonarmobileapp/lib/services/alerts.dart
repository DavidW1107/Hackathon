import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

/// Local notifications for motion alerts. No cloud push — the phone polls the
/// station directly, keeping the "air-gapped by design" promise.
class Alerts {
  Alerts._();

  static final _plugin = FlutterLocalNotificationsPlugin();
  static bool _ready = false;
  static int _id = 0;

  static Future<void> init() async {
    if (kIsWeb) return; // plugin has no web support; app still runs
    try {
      await _plugin.initialize(const InitializationSettings(
        android: AndroidInitializationSettings('@mipmap/ic_launcher'),
        iOS: DarwinInitializationSettings(),
      ));
      await _plugin
          .resolvePlatformSpecificImplementation<
              AndroidFlutterLocalNotificationsPlugin>()
          ?.requestNotificationsPermission();
      _ready = true;
    } catch (e) {
      debugPrint('Alerts init failed: $e');
    }
  }

  static Future<void> motion({required double range}) async {
    if (!_ready) return;
    final where = range < 1.0
        ? '${(range * 100).round()} cm'
        : '${range.toStringAsFixed(1)} m';
    try {
      await _plugin.show(
        _id++,
        'Movement detected',
        'Something moved about $where from the station.',
        const NotificationDetails(
          android: AndroidNotificationDetails(
            'motion_alerts',
            'Motion alerts',
            channelDescription: 'Fired when the station detects movement',
            importance: Importance.max,
            priority: Priority.high,
            category: AndroidNotificationCategory.alarm,
          ),
          iOS: DarwinNotificationDetails(
            presentAlert: true,
            presentBanner: true,
            presentSound: true,
          ),
        ),
      );
    } catch (e) {
      debugPrint('Alerts show failed: $e');
    }
  }
}
