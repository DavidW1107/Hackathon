import 'package:flutter/material.dart';
import '../theme/sentra_theme.dart';
import '../widgets/radar_display.dart';
import '../widgets/sentra_widgets.dart';

class RadarScreen extends StatefulWidget {
  const RadarScreen({super.key, required this.armed});

  final bool armed;

  @override
  State<RadarScreen> createState() => _RadarScreenState();
}

class _RadarScreenState extends State<RadarScreen> {
  // Detection distance gate, in metres. Anything at or beyond
  // [_maxDistance] means "Max" — no cap; the sonar sweeps its full range.
  // This value is what gets pushed to the sonar station (sensor.py
  // MAX_RANGE) once the app is wired up.
  static const _minDistance = 0.4; // 40 cm
  static const _maxDistance = 4.0;
  double _distance = 3.1;

  bool get _atMax => _distance >= _maxDistance;

  String get _distanceLabel => _atMax
      ? 'Max'
      : _distance < 1.0
          ? '${(_distance * 100).round()} cm'
          : '${_distance.toStringAsFixed(1)} m';

  // Positions sit inside the upward scan cone (~32° either side of vertical).
  static const _blips = [
    Blip(
      dx: 0.30,
      dy: -0.52,
      color: Sentra.greenBright,
      pingOffset: 0.0,
      label: '◦ presence · 2.4m',
    ),
    Blip(
      dx: -0.32,
      dy: -0.62,
      color: Sentra.amber,
      pingOffset: 0.45,
      label: 'unknown device ◦',
      labelRight: true,
    ),
    Blip(dx: 0.06, dy: -0.32, color: Sentra.green, pingOffset: 0.7),
  ];

  @override
  Widget build(BuildContext context) {
    final armed = widget.armed;
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 28),
      children: [
        const Kicker('02 · Scanning'),
        const SizedBox(height: 10),
        Text('Room A4', style: Sentra.display(size: 30, height: 1.05)),
        const SizedBox(height: 6),
        Text(
          armed
              ? 'Projecting an acoustic cone up from this station. Blips are reflectors picked out of the static room.'
              : 'Station on standby. Arm it to resume the acoustic sweep.',
          style: Sentra.sans(size: 13.5, height: 1.55),
        ),
        const SizedBox(height: 24),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8),
          child: RadarDisplay(blips: armed ? _blips : const [], armed: armed),
        ),
        const SizedBox(height: 28),
        _distanceSlider(),
        const SizedBox(height: 16),
        _lastEvent(),
      ],
    );
  }

  Widget _distanceSlider() {
    return Panel(
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 10),
      borderColor: Sentra.lineGreen,
      color: Sentra.bgRaise,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('DETECTION DISTANCE',
                  style: Sentra.mono(
                      size: 9.5, color: Sentra.inkDim, spacing: 1.4)),
              const Spacer(),
              Text(_distanceLabel,
                  style: Sentra.mono(
                      size: 12,
                      color: Sentra.greenBright,
                      weight: FontWeight.w600)),
            ],
          ),
          const SizedBox(height: 4),
          SliderTheme(
            data: SliderThemeData(
              trackHeight: 7,
              trackShape: const RectangularSliderTrackShape(),
              activeTrackColor: Sentra.green,
              inactiveTrackColor: Sentra.lineWhite,
              thumbColor: Sentra.greenBright,
              overlayColor: Sentra.green.withValues(alpha: 0.12),
              thumbShape: const _BlockThumbShape(),
              tickMarkShape: SliderTickMarkShape.noTickMark,
              overlayShape:
                  const RoundSliderOverlayShape(overlayRadius: 18),
            ),
            child: Slider(
              value: _distance,
              min: _minDistance,
              max: _maxDistance,
              divisions:
                  ((_maxDistance - _minDistance) / 0.1).round(), // 10 cm steps
              onChanged: (v) => setState(() => _distance = v),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4),
            child: Row(
              children: [
                Text('40 cm',
                    style: Sentra.mono(size: 9, color: Sentra.inkFaint)),
                const Spacer(),
                Text('MAX',
                    style: Sentra.mono(size: 9, color: Sentra.inkFaint)),
              ],
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
