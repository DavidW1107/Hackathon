import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import '../theme/sentra_theme.dart';
import '../widgets/radar_display.dart';
import '../widgets/sentra_widgets.dart';

/// Python sonar sensor endpoint. Same machine -> localhost. For a phone, set
/// this to the laptop's LAN IP, e.g. '192.168.1.42:8765' (sensor binds 0.0.0.0).
const String kSensorHost = 'localhost:8765';

class RadarScreen extends StatefulWidget {
  const RadarScreen({super.key, required this.armed});

  final bool armed;

  @override
  State<RadarScreen> createState() => _RadarScreenState();
}

class _RadarScreenState extends State<RadarScreen> {
  Timer? _timer;
  List<Blip> _blips = const [];
  int _count = 0;
  double _maxRange = 2.0;
  double _fov = 50;
  bool _live = false;

  @override
  void initState() {
    super.initState();
    if (widget.armed) _start();
  }

  @override
  void didUpdateWidget(RadarScreen old) {
    super.didUpdateWidget(old);
    if (widget.armed && _timer == null) _start();
    if (!widget.armed) _stop();
  }

  @override
  void dispose() {
    _stop();
    super.dispose();
  }

  void _start() =>
      _timer = Timer.periodic(const Duration(milliseconds: 150), (_) => _poll());

  void _stop() {
    _timer?.cancel();
    _timer = null;
    if (mounted) setState(() => _live = false);
  }

  Future<void> _poll() async {
    try {
      final r = await http
          .get(Uri.parse('http://$kSensorHost/'))
          .timeout(const Duration(milliseconds: 400));
      if (r.statusCode != 200) throw 'status ${r.statusCode}';
      final j = jsonDecode(r.body) as Map<String, dynamic>;
      final maxR = (j['max_range'] as num?)?.toDouble() ?? 2.0;
      final fov = (j['fov'] as num?)?.toDouble() ?? 50;
      final tgts = (j['targets'] as List?) ?? const [];
      final blips = <Blip>[];
      for (var i = 0; i < tgts.length; i++) {
        final t = tgts[i] as Map<String, dynamic>;
        final range = (t['range'] as num).toDouble();
        final az = (t['az'] as num).toDouble() * math.pi / 180.0;
        final frac = (range / maxR).clamp(0.0, 1.0);
        blips.add(Blip(
          dx: frac * math.sin(az),
          dy: -frac * math.cos(az), // up = further from the sensor
          color: Sentra.greenBright,
          pingOffset: (i * 0.33) % 1.0,
          label: '◦ ${range.toStringAsFixed(1)}m',
        ));
      }
      if (!mounted) return;
      setState(() {
        _blips = blips;
        _count = tgts.length;
        _maxRange = maxR;
        _fov = fov;
        _live = true;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _blips = const [];
        _count = 0;
        _live = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final armed = widget.armed;
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 28),
      children: [
        const Kicker('02 · Live sweep'),
        const SizedBox(height: 10),
        Text('Room A4', style: Sentra.display(size: 30, height: 1.05)),
        const SizedBox(height: 6),
        Text(
          armed
              ? 'Sweeping the space in front of the station. Green pings are moving reflectors picked out of the static room, in real time.'
              : 'Station on standby. Arm it to resume the acoustic sweep.',
          style: Sentra.sans(size: 13.5, height: 1.55),
        ),
        const SizedBox(height: 24),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8),
          child: RadarDisplay(
            blips: armed ? _blips : const [],
            fovDeg: _fov,
            armed: armed,
          ),
        ),
        const SizedBox(height: 28),
        _readouts(),
        const SizedBox(height: 16),
        _statusRow(armed),
      ],
    );
  }

  Widget _readouts() {
    return Panel(
      padding: const EdgeInsets.symmetric(vertical: 22, horizontal: 20),
      borderColor: Sentra.lineGreen,
      color: Sentra.bgRaise,
      child: Row(
        children: [
          const Expanded(
            child: StatTile(value: '17-21', unit: 'kHz', label: 'Sweep freq'),
          ),
          const _Divider(),
          Expanded(
            child: StatTile(
              value: _maxRange.toStringAsFixed(1),
              unit: 'm',
              label: 'Range gate',
            ),
          ),
          const _Divider(),
          Expanded(
            child: StatTile(
              value: '$_count',
              label: 'Targets',
              valueColor: _count > 0 ? Sentra.greenBright : Sentra.ink,
            ),
          ),
        ],
      ),
    );
  }

  Widget _statusRow(bool armed) {
    final color = !armed
        ? Sentra.inkDim
        : (_live ? Sentra.greenBright : Sentra.amber);
    return Panel(
      borderColor: Sentra.lineGreenMid,
      color: Sentra.bgPanel,
      child: Row(
        children: [
          Container(
            width: 10,
            height: 10,
            decoration: BoxDecoration(shape: BoxShape.circle, color: color),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              !armed
                  ? 'Standby'
                  : (_live
                      ? 'Live · $kSensorHost'
                      : 'No signal · start sensor.py'),
              style: Sentra.sans(
                  size: 13.5, weight: FontWeight.w500, color: Sentra.ink),
            ),
          ),
          Text(
            _live ? '$_count moving' : '—',
            style: Sentra.mono(size: 11, color: Sentra.inkDim),
          ),
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
