import 'package:flutter/material.dart';
import '../theme/sentra_theme.dart';
import '../widgets/sentra_widgets.dart';

/// One entry in the activity feed, in plain English.
class _Event {
  const _Event({
    required this.icon,
    required this.color,
    required this.title,
    required this.detail,
    required this.time,
    this.isAlert = false,
  });

  final IconData icon;
  final Color color;
  final String title;
  final String detail;
  final String time;
  final bool isAlert;
}

class EventsScreen extends StatelessWidget {
  const EventsScreen({super.key});

  static const _events = [
    _Event(
      icon: Icons.person_outline,
      color: Sentra.greenBright,
      title: 'You were recognized',
      detail: 'Walking pattern matched — welcome home.',
      time: '1:58 PM',
    ),
    _Event(
      icon: Icons.check_circle_outline,
      color: Sentra.green,
      title: 'Back to normal',
      detail: 'Movement stopped. Monitoring resumed.',
      time: '1:58 PM',
    ),
    _Event(
      icon: Icons.notifications_active_outlined,
      color: Sentra.amber,
      title: 'Alert sent to your phone',
      detail: 'A snapshot was saved on this device.',
      time: '1:58 PM',
      isAlert: true,
    ),
    _Event(
      icon: Icons.directions_walk,
      color: Sentra.amber,
      title: 'Movement detected',
      detail: 'Something moved about 2.4 m from the sensor.',
      time: '1:58 PM',
      isAlert: true,
    ),
    _Event(
      icon: Icons.radar,
      color: Sentra.green,
      title: 'Room scanned',
      detail: 'Everything looked normal.',
      time: '1:58 PM',
    ),
  ];

  @override
  Widget build(BuildContext context) {
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
        const Kicker('Today', color: Sentra.inkDim),
        const SizedBox(height: 12),
        for (final e in _events) ...[
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
  }

  Widget _statusBanner() {
    return Panel(
      padding: const EdgeInsets.all(18),
      borderColor: Sentra.lineGreenMid,
      glow: true,
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: Sentra.green.withValues(alpha: 0.12),
            ),
            child: const Icon(Icons.shield_outlined,
                size: 22, color: Sentra.green),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('All clear right now',
                    style: Sentra.sans(
                        size: 15.5, weight: FontWeight.w600, color: Sentra.ink)),
                const SizedBox(height: 3),
                Text('SENTRA is watching your space.',
                    style: Sentra.sans(size: 12.5)),
              ],
            ),
          ),
          const StatusPill(label: 'Live'),
        ],
      ),
    );
  }

  Widget _eventCard(_Event e) {
    return Panel(
      padding: const EdgeInsets.all(14),
      borderColor: e.isAlert
          ? Sentra.amber.withValues(alpha: 0.35)
          : Sentra.lineWhite,
      child: Row(
        children: [
          Container(
            width: 38,
            height: 38,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: e.color.withValues(alpha: 0.12),
            ),
            child: Icon(e.icon, size: 19, color: e.color),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(e.title,
                    style: Sentra.sans(
                        size: 14, weight: FontWeight.w600, color: Sentra.ink)),
                const SizedBox(height: 3),
                Text(e.detail, style: Sentra.sans(size: 12.5, height: 1.4)),
              ],
            ),
          ),
          const SizedBox(width: 10),
          Text(e.time,
              style: Sentra.mono(size: 10, color: Sentra.inkFaint)),
        ],
      ),
    );
  }
}
