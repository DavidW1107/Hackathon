import 'package:flutter/material.dart';
import '../theme/sentra_theme.dart';

/// The rotated-square brand mark used in the nav and footer of the site.
class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.size = 14, this.glow = true});

  final double size;
  final bool glow;

  @override
  Widget build(BuildContext context) {
    return Transform.rotate(
      angle: 0.785398, // 45°
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          color: Sentra.green,
          boxShadow: glow
              ? [
                  BoxShadow(
                    color: Sentra.green.withValues(alpha: 0.7),
                    blurRadius: 12,
                  ),
                ]
              : null,
        ),
      ),
    );
  }
}

/// Uppercase mono "kicker" — e.g. `01 · THE PLATFORM`.
class Kicker extends StatelessWidget {
  const Kicker(this.text, {super.key, this.color = Sentra.green});

  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Text(
      text.toUpperCase(),
      style: Sentra.mono(
        size: 11,
        weight: FontWeight.w600,
        color: color,
        spacing: 1.8,
      ),
    );
  }
}

/// Pill with a pulsing dot — the "LIVE ACOUSTIC SWEEP" badge.
class StatusPill extends StatefulWidget {
  const StatusPill({
    super.key,
    required this.label,
    this.color = Sentra.green,
    this.pulse = true,
  });

  final String label;
  final Color color;
  final bool pulse;

  @override
  State<StatusPill> createState() => _StatusPillState();
}

class _StatusPillState extends State<StatusPill>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c;

  @override
  void initState() {
    super.initState();
    _c = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1600),
    );
    if (widget.pulse) _c.repeat(reverse: true);
  }

  @override
  void didUpdateWidget(StatusPill old) {
    super.didUpdateWidget(old);
    if (widget.pulse && !_c.isAnimating) {
      _c.repeat(reverse: true);
    } else if (!widget.pulse && _c.isAnimating) {
      _c.stop();
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(11, 6, 13, 6),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(100),
        border: Border.all(color: widget.color.withValues(alpha: 0.28)),
        color: widget.color.withValues(alpha: 0.05),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          FadeTransition(
            opacity: widget.pulse
                ? Tween(begin: 0.45, end: 1.0).animate(_c)
                : const AlwaysStoppedAnimation(1),
            child: Container(
              width: 6,
              height: 6,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: widget.color,
                boxShadow: [
                  BoxShadow(color: widget.color, blurRadius: 8),
                ],
              ),
            ),
          ),
          const SizedBox(width: 8),
          Text(
            widget.label.toUpperCase(),
            style: Sentra.mono(
              size: 10.5,
              color: widget.color,
              spacing: 1.6,
            ),
          ),
        ],
      ),
    );
  }
}

/// A bordered panel with the subtle green edge used across the site cards.
class Panel extends StatelessWidget {
  const Panel({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(20),
    this.color = Sentra.bgRaise,
    this.borderColor = Sentra.lineWhite,
    this.radius = 14,
    this.glow = false,
  });

  final Widget child;
  final EdgeInsets padding;
  final Color color;
  final Color borderColor;
  final double radius;
  final bool glow;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: padding,
      decoration: BoxDecoration(
        color: color,
        borderRadius: BorderRadius.circular(radius),
        border: Border.all(color: borderColor),
        boxShadow: glow
            ? [
                BoxShadow(
                  color: Sentra.green.withValues(alpha: 0.09),
                  blurRadius: 48,
                ),
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.5),
                  blurRadius: 60,
                  offset: const Offset(0, 24),
                ),
              ]
            : null,
      ),
      child: child,
    );
  }
}

/// Small stat readout — big display number + mono caption.
class StatTile extends StatelessWidget {
  const StatTile({
    super.key,
    required this.value,
    required this.label,
    this.unit,
    this.valueColor = Sentra.greenBright,
  });

  final String value;
  final String? unit;
  final String label;
  final Color valueColor;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        RichText(
          text: TextSpan(
            text: value,
            style: Sentra.display(size: 26, color: valueColor, spacing: -0.6),
            children: [
              if (unit != null)
                TextSpan(
                  text: unit,
                  style: Sentra.display(
                    size: 14,
                    color: valueColor,
                    weight: FontWeight.w500,
                  ),
                ),
            ],
          ),
        ),
        const SizedBox(height: 8),
        Text(
          label.toUpperCase(),
          style: Sentra.mono(size: 9.5, color: Sentra.inkDim, spacing: 1.4),
        ),
      ],
    );
  }
}
