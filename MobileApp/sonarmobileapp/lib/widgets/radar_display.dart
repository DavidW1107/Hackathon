import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../theme/sentra_theme.dart';

/// A detected reflector on the sweep.
class Blip {
  const Blip({
    required this.dx,
    required this.dy,
    required this.color,
    this.pingOffset = 0,
    this.label,
    this.labelRight = false,
  });

  /// Position as a fraction of the radar radius, relative to centre.
  final double dx;
  final double dy;
  final Color color;

  /// Phase offset so blips don't ping in lock-step.
  final double pingOffset;

  final String? label;
  final bool labelRight;
}

/// The SONR radar — rings, cross-hairs, a rotating conic sweep, a "SONR"
/// core, and animated ping blips. Mirrors the landing-page hero radar.
class RadarDisplay extends StatefulWidget {
  const RadarDisplay({
    super.key,
    required this.blips,
    this.armed = true,
  });

  final List<Blip> blips;
  final bool armed;

  @override
  State<RadarDisplay> createState() => _RadarDisplayState();
}

class _RadarDisplayState extends State<RadarDisplay>
    with TickerProviderStateMixin {
  late final AnimationController _sweep = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 4500),
  )..repeat();

  late final AnimationController _ping = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2600),
  )..repeat();

  @override
  void dispose() {
    _sweep.dispose();
    _ping.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AspectRatio(
      aspectRatio: 1,
      child: LayoutBuilder(
        builder: (context, c) {
          final side = math.min(c.maxWidth, c.maxHeight);
          final radius = side / 2;
          return Stack(
            clipBehavior: Clip.none,
            children: [
              AnimatedBuilder(
                animation: Listenable.merge([_sweep, _ping]),
                builder: (context, _) {
                  return CustomPaint(
                    size: Size(side, side),
                    painter: _RadarPainter(
                      sweep: _sweep.value * 2 * math.pi,
                      ping: _ping.value,
                      armed: widget.armed,
                      blips: widget.blips,
                    ),
                  );
                },
              ),
              // core chip
              Center(
                child: Container(
                  width: 66,
                  height: 44,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: Sentra.lineGreenHot),
                    gradient: const LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: [Sentra.bgPanel, Color(0xFF0B100E)],
                    ),
                    boxShadow: [
                      BoxShadow(
                        color: Sentra.green.withValues(alpha: 0.35),
                        blurRadius: 26,
                      ),
                    ],
                  ),
                  child: Text(
                    'SONR',
                    style: Sentra.mono(
                      size: 9,
                      color: Sentra.green,
                      spacing: 1.2,
                    ),
                  ),
                ),
              ),
              // blip labels
              for (final b in widget.blips)
                if (b.label != null)
                  _blipLabel(b, radius),
            ],
          );
        },
      ),
    );
  }

  Widget _blipLabel(Blip b, double radius) {
    final cx = radius + b.dx * radius;
    final cy = radius + b.dy * radius;
    const gap = 16.0;
    return Positioned(
      left: b.labelRight ? null : cx + gap,
      right: b.labelRight ? (2 * radius) - (cx - gap) : null,
      top: cy - 7,
      child: Text(
        b.label!,
        style: Sentra.mono(
          size: 10,
          color: b.color == Sentra.amber ? Sentra.amber : Sentra.greenSoft,
        ),
      ),
    );
  }
}

class _RadarPainter extends CustomPainter {
  _RadarPainter({
    required this.sweep,
    required this.ping,
    required this.armed,
    required this.blips,
  });

  final double sweep;
  final double ping;
  final bool armed;
  final List<Blip> blips;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final r = size.width / 2;

    // soft radial glow behind everything
    canvas.drawCircle(
      center,
      r,
      Paint()
        ..shader = RadialGradient(
          colors: [
            Sentra.green.withValues(alpha: armed ? 0.12 : 0.05),
            Colors.transparent,
          ],
          stops: const [0.0, 0.7],
        ).createShader(Rect.fromCircle(center: center, radius: r)),
    );

    // concentric rings
    for (int i = 0; i < 4; i++) {
      final rr = r * (1 - i * 0.235);
      canvas.drawCircle(
        center,
        rr,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1
          ..color = Sentra.green.withValues(alpha: 0.12 + i * 0.06),
      );
    }

    // cross-hairs
    final axis = Paint()
      ..color = Sentra.green.withValues(alpha: 0.12)
      ..strokeWidth = 1;
    canvas.drawLine(
      Offset(center.dx, center.dy - r),
      Offset(center.dx, center.dy + r),
      axis,
    );
    canvas.drawLine(
      Offset(center.dx - r, center.dy),
      Offset(center.dx + r, center.dy),
      axis,
    );

    // rotating conic sweep
    if (armed) {
      final sweepPaint = Paint()
        ..shader = SweepGradient(
          transform: GradientRotation(sweep - math.pi / 2),
          colors: [
            Sentra.green.withValues(alpha: 0.42),
            Sentra.green.withValues(alpha: 0.05),
            Colors.transparent,
            Colors.transparent,
          ],
          stops: const [0.0, 0.40, 0.55, 1.0],
        ).createShader(Rect.fromCircle(center: center, radius: r));
      canvas.drawCircle(center, r, sweepPaint);

      // bright leading edge
      final lead = Offset(
        center.dx + math.cos(sweep - math.pi / 2) * r,
        center.dy + math.sin(sweep - math.pi / 2) * r,
      );
      canvas.drawLine(
        center,
        lead,
        Paint()
          ..strokeWidth = 1.5
          ..shader = LinearGradient(
            colors: [
              Sentra.greenBright.withValues(alpha: 0.7),
              Colors.transparent,
            ],
          ).createShader(Rect.fromPoints(center, lead)),
      );
    }

    // blips
    for (final b in blips) {
      final p = Offset(center.dx + b.dx * r, center.dy + b.dy * r);

      // expanding ping ring
      final phase = (ping + b.pingOffset) % 1.0;
      final ringR = 6 + phase * 26;
      canvas.drawCircle(
        p,
        ringR,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1
          ..color = b.color.withValues(alpha: (1 - phase) * 0.55),
      );

      // glow + core
      canvas.drawCircle(
        p,
        7,
        Paint()..color = b.color.withValues(alpha: 0.25),
      );
      canvas.drawCircle(
        p,
        4,
        Paint()
          ..color = b.color
          ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 4),
      );
      canvas.drawCircle(p, 3.2, Paint()..color = b.color);
    }
  }

  @override
  bool shouldRepaint(_RadarPainter old) =>
      old.sweep != sweep || old.ping != ping || old.armed != armed;
}
