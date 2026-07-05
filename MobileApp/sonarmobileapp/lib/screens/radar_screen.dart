import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../services/sensor_service.dart';
import '../theme/sentra_theme.dart';
import '../widgets/radar_display.dart';
import '../widgets/sentra_widgets.dart';

class RadarScreen extends StatefulWidget {
  const RadarScreen({super.key, required this.sensor});

  final SensorService sensor;

  @override
  State<RadarScreen> createState() => _RadarScreenState();
}

class _RadarScreenState extends State<RadarScreen> {
  // Field-of-view cone half-angle (deg), pushed live to the station
  // (sensor.py /config?fov=) — it clips how far off-axis echoes are trusted.
  static const _minFov = 20.0;
  static const _maxFov = 80.0;
  static const _fovStep = 5.0;
  static final _fovSteps = ((_maxFov - _minFov) / _fovStep).round();

  double _fov = 80;
  bool _fovDragging = false;

  List<Blip> _blips(SensorService s) {
    final blips = <Blip>[];
    for (var i = 0; i < s.targets.length; i++) {
      final t = s.targets[i];
      final az = t.az * math.pi / 180.0;
      final frac = (t.range / s.maxRange).clamp(0.0, 1.0);
      // sqrt stretches near ranges outward and a floor keeps blips clear of
      // the SONR core chip — otherwise close echoes sit on top of the centre.
      final rr = 0.22 + 0.78 * math.sqrt(frac);
      blips.add(Blip(
        dx: rr * math.sin(az),
        dy: -rr * math.cos(az), // up = further from the sensor
        color: Sentra.greenBright,
        pingOffset: (i * 0.33) % 1.0,
        label: '◦ ${t.range.toStringAsFixed(1)}m',
      ));
    }
    return blips;
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: widget.sensor,
      builder: (context, _) {
        final s = widget.sensor;
        final armed = s.armed;
        // Mirror the station's live settings unless the user is mid-drag.
        if (!_fovDragging && s.live) {
          _fov = ((s.fov / _fovStep).roundToDouble() * _fovStep)
              .clamp(_minFov, _maxFov);
        }
        return ListView(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 28),
          children: [
            const Kicker('02 · Scanning'),
            const SizedBox(height: 10),
            Text('Room A4', style: Sentra.display(size: 30, height: 1.05)),
            const SizedBox(height: 6),
            Text(
              armed
                  ? 'Sweeping the space in front of the station. Green pings are moving reflectors picked out of the static room, in real time.'
                  : 'Station paused. Tap play to resume the acoustic sweep.',
              style: Sentra.sans(size: 13.5, height: 1.55),
            ),
            const SizedBox(height: 24),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8),
              child: Stack(
                alignment: Alignment.center,
                children: [
                  RadarDisplay(
                    blips: armed ? _blips(s) : const [],
                    fovDeg: s.fov,
                    armed: armed,
                  ),
                  if (!armed) _playButton(s),
                ],
              ),
            ),
            const SizedBox(height: 28),
            _fovSlider(s),
            const SizedBox(height: 16),
            _statusRow(s),
          ],
        );
      },
    );
  }

  // Play-only by design: resuming is one tap here; pausing lives on the
  // Station tab so a stray tap on the radar can't silence the alarm.
  Widget _playButton(SensorService s) {
    return GestureDetector(
      onTap: () => s.setArmed(true),
      child: Container(
        width: 72,
        height: 72,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: Sentra.bgPanel,
          border: Border.all(color: Sentra.lineGreenHot),
          boxShadow: [
            BoxShadow(
              color: Sentra.green.withValues(alpha: 0.35),
              blurRadius: 26,
            ),
          ],
        ),
        child: const Icon(Icons.play_arrow_rounded,
            size: 40, color: Sentra.green),
      ),
    );
  }

  Widget _fovSlider(SensorService s) {
    return Panel(
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 10),
      borderColor: Sentra.lineGreen,
      color: Sentra.bgRaise,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('FIELD OF VIEW',
                  style: Sentra.mono(
                      size: 9.5, color: Sentra.inkDim, spacing: 1.4)),
              const Spacer(),
              Text('±${_fov.round()}°',
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
              trackShape: _BlockTrackShape(blockCount: _fovSteps),
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
              value: _fov.clamp(_minFov, _maxFov),
              min: _minFov,
              max: _maxFov,
              divisions: _fovSteps, // hard snap, 5° per block
              onChangeStart: (_) => _fovDragging = true,
              onChanged: (v) => setState(() => _fov = v),
              onChangeEnd: (v) {
                _fovDragging = false;
                setState(() => _fov = v);
                s.setFov(v);
              },
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4),
            child: Row(
              children: [
                Text('±20°',
                    style: Sentra.mono(size: 9, color: Sentra.inkFaint)),
                const Spacer(),
                Text('±80°',
                    style: Sentra.mono(size: 9, color: Sentra.inkFaint)),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _statusRow(SensorService s) {
    final armed = s.armed;
    final color = !armed
        ? Sentra.inkDim
        : (s.live ? Sentra.greenBright : Sentra.amber);
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
                  : (s.live
                      ? 'Live · ${s.host}'
                      : 'No signal · start sensor.py'),
              style: Sentra.sans(
                  size: 13.5, weight: FontWeight.w500, color: Sentra.ink),
            ),
          ),
          Text(
            s.live ? '${s.targets.length} moving' : '—',
            style: Sentra.mono(size: 11, color: Sentra.inkDim),
          ),
        ],
      ),
    );
  }
}

/// Slim rectangular slider thumb matching the blocky SONR aesthetic.
class _BlockThumbShape extends SliderComponentShape {
  const _BlockThumbShape();

  static const _size = Size(12, 22);

  @override
  Size getPreferredSize(bool isEnabled, bool isDiscrete) => _size;

  @override
  void paint(
    PaintingContext context,
    Offset center, {
    required Animation<double> activationAnimation,
    required Animation<double> enableAnimation,
    required bool isDiscrete,
    required TextPainter labelPainter,
    required RenderBox parentBox,
    required SliderThemeData sliderTheme,
    required TextDirection textDirection,
    required double value,
    required double textScaleFactor,
    required Size sizeWithOverflow,
  }) {
    final rect = Rect.fromCenter(
        center: center, width: _size.width, height: _size.height);
    context.canvas.drawRRect(
      RRect.fromRectAndRadius(rect, const Radius.circular(2)),
      Paint()..color = sliderTheme.thumbColor ?? Sentra.greenBright,
    );
  }
}

/// Track drawn as one discrete block per snap step, filled up to the thumb.
class _BlockTrackShape extends SliderTrackShape with BaseSliderTrackShape {
  const _BlockTrackShape({required this.blockCount});

  final int blockCount;

  static const _gap = 2.0;

  @override
  void paint(
    PaintingContext context,
    Offset offset, {
    required RenderBox parentBox,
    required SliderThemeData sliderTheme,
    required Animation<double> enableAnimation,
    required TextDirection textDirection,
    required Offset thumbCenter,
    Offset? secondaryOffset,
    bool isDiscrete = false,
    bool isEnabled = false,
  }) {
    final rect = getPreferredRect(
      parentBox: parentBox,
      offset: offset,
      sliderTheme: sliderTheme,
      isEnabled: isEnabled,
      isDiscrete: isDiscrete,
    );
    final blockWidth = (rect.width - _gap * (blockCount - 1)) / blockCount;
    final canvas = context.canvas;
    for (var i = 0; i < blockCount; i++) {
      final block = Rect.fromLTWH(
        rect.left + i * (blockWidth + _gap),
        rect.top,
        blockWidth,
        rect.height,
      );
      final active = block.center.dx <= thumbCenter.dx;
      canvas.drawRect(
        block,
        Paint()
          ..color = active
              ? (sliderTheme.activeTrackColor ?? Sentra.green)
              : (sliderTheme.inactiveTrackColor ?? Sentra.lineWhite),
      );
    }
  }
}
