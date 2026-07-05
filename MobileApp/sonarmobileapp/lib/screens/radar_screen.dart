import 'package:flutter/material.dart';
import '../theme/sentra_theme.dart';
import '../widgets/radar_display.dart';
import '../widgets/sentra_widgets.dart';

class RadarScreen extends StatelessWidget {
  const RadarScreen({super.key, required this.armed});

  final bool armed;

  static const _blips = [
    Blip(
      dx: 0.40,
      dy: -0.40,
      color: Sentra.greenBright,
      pingOffset: 0.0,
      label: '◦ presence · 2.4m',
    ),
    Blip(
      dx: -0.48,
      dy: 0.28,
      color: Sentra.amber,
      pingOffset: 0.45,
      label: 'unknown device ◦',
      labelRight: true,
    ),
    Blip(dx: 0.24, dy: 0.46, color: Sentra.green, pingOffset: 0.7),
  ];

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 28),
      children: [
        const Kicker('02 · Live sweep'),
        const SizedBox(height: 10),
        Text('Room A4', style: Sentra.display(size: 30, height: 1.05)),
        const SizedBox(height: 6),
        Text(
          armed
              ? 'Sweeping the space around this station. Blips are reflectors picked out of the static room.'
              : 'Station on standby. Arm it to resume the acoustic sweep.',
          style: Sentra.sans(size: 13.5, height: 1.55),
        ),
        const SizedBox(height: 24),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8),
          child: RadarDisplay(blips: armed ? _blips : const [], armed: armed),
        ),
        const SizedBox(height: 28),
        _readouts(),
        const SizedBox(height: 16),
        _lastEvent(),
      ],
    );
  }

  Widget _readouts() {
    return Panel(
      padding: const EdgeInsets.symmetric(vertical: 22, horizontal: 20),
      borderColor: Sentra.lineGreen,
      color: Sentra.bgRaise,
      child: Row(
        children: const [
          Expanded(
            child: StatTile(value: '40', unit: 'kHz', label: 'Sweep freq'),
          ),
          _Divider(),
          Expanded(
            child: StatTile(value: '3.1', unit: 'm', label: 'Range gate'),
          ),
          _Divider(),
          Expanded(
            child: StatTile(
              value: '2',
              label: 'Targets',
              valueColor: Sentra.amber,
            ),
          ),
        ],
      ),
    );
  }

  Widget _lastEvent() {
    return Panel(
      borderColor: Sentra.lineGreenMid,
      color: Sentra.bgPanel,
      child: Row(
        children: [
          Container(
            width: 38,
            height: 38,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(9),
              color: Sentra.amber.withValues(alpha: 0.12),
              border: Border.all(color: Sentra.amber.withValues(alpha: 0.4)),
            ),
            child: const Icon(Icons.warning_amber_rounded,
                color: Sentra.amber, size: 18),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Presence flagged @ 2.4m',
                    style: Sentra.sans(
                        size: 13.5,
                        weight: FontWeight.w500,
                        color: Sentra.ink)),
                const SizedBox(height: 3),
                Text('bearing 041° · push sent · snapshot saved',
                    style: Sentra.mono(size: 10.5, color: Sentra.inkFaint)),
              ],
            ),
          ),
          Text('13:58', style: Sentra.mono(size: 11, color: Sentra.inkDim)),
        ],
      ),
    );
  }
}

class _Divider extends StatelessWidget {
  const _Divider();
  @override
  Widget build(BuildContext context) =>
      Container(width: 1, height: 40, color: Sentra.lineGreen);
}
