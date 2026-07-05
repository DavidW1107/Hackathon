import 'package:flutter/material.dart';
import '../services/sensor_service.dart';
import '../theme/sentra_theme.dart';
import '../widgets/sentra_widgets.dart';

/// Live activity feed — real motion events streamed from the station.
class EventsScreen extends StatelessWidget {
  const EventsScreen({super.key, required this.sensor});

  final SensorService sensor;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: sensor,
      builder: (context, _) {
        final events = sensor.events;
        return ListView(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 28),
          children: [
            const Kicker('Activity'),
            const SizedBox(height: 10),
            Text('What happened today',
                style: Sentra.display(size: 30, height: 1.05)),
            const SizedBox(height: 22),
            _statusBanner(),
            const SizedBox(height: 26),
            const Kicker('Detections', color: Sentra.inkDim),
            const SizedBox(height: 12),
            if (events.isEmpty)
              _emptyCard()
            else
              for (final e in events) ...[
                _eventCard(e),
                const SizedBox(height: 10),
              ],
            const SizedBox(height: 10),
            Center(
              child: Text(
                'All activity stays on this device — nothing is uploaded.',
                textAlign: TextAlign.center,
                style: Sentra.sans(size: 12, color: Sentra.inkFaint),
              ),
            ),
          ],
        );
      },
    );
  }

  Widget _statusBanner() {
    final moving = sensor.targets.isNotEmpty;
    final live = sensor.live;
    final watching = live && sensor.armed;
    final (color, icon, title, detail, pill) = !live
        ? (
            Sentra.inkDim,
            Icons.sensors_off_outlined,
            'Station offline',
            'No signal from the sonar — start sensor.py.',
            'Offline'
          )
        : !sensor.armed
            ? (
                Sentra.inkDim,
                Icons.pause_circle_outline,
                'Detection paused',
                'The station is on standby — no detections or alerts until you arm it.',
                'Paused'
              )
            : moving
                ? (
                    Sentra.amber,
                    Icons.directions_walk,
                    'Movement right now',
                    'Something is moving about ${sensor.targets.first.range.toStringAsFixed(1)} m away.',
                    'Motion'
                  )
                : (
                    Sentra.green,
                    Icons.shield_outlined,
                    'All clear right now',
                    'SENTRA is watching your space.',
                    'Live'
                  );
    return Panel(
      padding: const EdgeInsets.all(18),
      borderColor: watching ? Sentra.lineGreenMid : Sentra.lineWhite,
      glow: watching,
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: color.withValues(alpha: 0.12),
            ),
            child: Icon(icon, size: 22, color: color),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title,
                    style: Sentra.sans(
                        size: 15.5, weight: FontWeight.w600, color: Sentra.ink)),
                const SizedBox(height: 3),
                Text(detail, style: Sentra.sans(size: 12.5)),
              ],
            ),
          ),
          StatusPill(label: pill, color: color, pulse: watching),
        ],
      ),
    );
  }

  Widget _emptyCard() {
    return Panel(
      padding: const EdgeInsets.all(18),
      borderColor: Sentra.lineWhite,
      child: Row(
        children: [
          const Icon(Icons.nightlight_outlined,
              size: 20, color: Sentra.inkFaint),
          const SizedBox(width: 14),
          Expanded(
            child: Text(
              'Nothing yet — motion events will appear here as the station detects them.',
              style: Sentra.sans(size: 12.5, height: 1.4),
            ),
          ),
        ],
      ),
    );
  }

  static String _clock(DateTime t) {
    final local = t.toLocal();
    final h = local.hour % 12 == 0 ? 12 : local.hour % 12;
    final m = local.minute.toString().padLeft(2, '0');
    return '$h:$m ${local.hour < 12 ? 'AM' : 'PM'}';
  }

  Widget _eventCard(SensorEvent e) {
    final where = e.range < 1.0
        ? '${(e.range * 100).round()} cm'
        : '${e.range.toStringAsFixed(1)} m';
    return Panel(
      padding: const EdgeInsets.all(14),
      borderColor: Sentra.amber.withValues(alpha: 0.35),
      child: Row(
        children: [
          Container(
            width: 38,
            height: 38,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: Sentra.amber.withValues(alpha: 0.12),
            ),
            child: const Icon(Icons.directions_walk,
                size: 19, color: Sentra.amber),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Movement detected',
                    style: Sentra.sans(
                        size: 14, weight: FontWeight.w600, color: Sentra.ink)),
                const SizedBox(height: 3),
                Text(
                  'Something moved about $where from the station.',
                  style: Sentra.sans(size: 12.5, height: 1.4),
                ),
              ],
            ),
          ),
          const SizedBox(width: 10),
          Text(_clock(e.wall),
              style: Sentra.mono(size: 10, color: Sentra.inkFaint)),
        ],
      ),
    );
  }
}
