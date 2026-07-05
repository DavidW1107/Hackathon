import 'package:flutter/material.dart';
import '../theme/sentra_theme.dart';
import '../widgets/sentra_widgets.dart';

/// One coloured token in a terminal line.
class _Tok {
  const _Tok(this.text, this.color, {this.weight = FontWeight.w400});
  final String text;
  final Color color;
  final FontWeight weight;
}

class EventsScreen extends StatelessWidget {
  const EventsScreen({super.key});

  static final _dim = Sentra.inkFaint;
  static const _grn = Sentra.green;
  static const _brt = Sentra.greenBright;
  static const _amb = Sentra.amber;
  static const _ink = Sentra.ink;

  List<List<_Tok>> get _log => [
        [_Tok('[13:58:02] ', _dim), _Tok('sweep 0397 ', _grn),
            _Tok('· ', _dim), _Tok('baseline locked', _ink)],
        [_Tok('[13:58:04] ', _dim), _Tok('MTI subtraction ', _ink),
            _Tok('· ', _dim), _Tok('static field clear', _grn)],
        [_Tok('[13:58:07] ', _dim), _Tok('Δ echo 2.4m ', _brt),
            _Tok('· ', _dim), _Tok('bearing 041° ', _ink),
            _Tok('· ', _dim), _Tok('presence', _brt)],
        [_Tok('[13:58:07] ', _dim), _Tok('ALERT ', _amb, weight: FontWeight.w600),
            _Tok('→ ', _dim), _Tok('push sent · snapshot saved', _ink)],
        [_Tok('[13:58:12] ', _dim), _Tok('range-gate 3.1m ', _ink),
            _Tok('· ', _dim), _Tok('profile aligned', _grn)],
        [_Tok('[13:58:15] ', _dim), _Tok('echo cleared ', _ink),
            _Tok('· ', _dim), _Tok('rearmed', _grn)],
        [_Tok('[13:58:18] ', _dim), _Tok('sweep 0398 ', _grn),
            _Tok('· ', _dim), _Tok('baseline locked', _ink)],
        [_Tok('[13:58:21] ', _dim), _Tok('cadence match 98.2% ', _brt),
            _Tok('· ', _dim), _Tok('login accepted', _ink)],
      ];

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 28),
      children: [
        const Kicker('Event log'),
        const SizedBox(height: 10),
        Text('Echo receipts', style: Sentra.display(size: 30, height: 1.05)),
        const SizedBox(height: 6),
        Text(
          'A timestamped, on-device record of every sweep and detection. '
          'Nothing but echoes ever leaves the station.',
          style: Sentra.sans(size: 13.5, height: 1.55),
        ),
        const SizedBox(height: 22),
        _terminal(),
        const SizedBox(height: 18),
        Row(
          children: [
            Expanded(child: _tally('4', 'SWEEPS', Sentra.green)),
            const SizedBox(width: 12),
            Expanded(child: _tally('1', 'ALERTS', Sentra.amber)),
            const SizedBox(width: 12),
            Expanded(child: _tally('0', 'PENDING', Sentra.inkDim)),
          ],
        ),
      ],
    );
  }

  Widget _terminal() {
    return Panel(
      padding: EdgeInsets.zero,
      color: Sentra.terminal,
      borderColor: Sentra.lineGreenMid,
      radius: 12,
      glow: true,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // title bar
          Container(
            padding: const EdgeInsets.fromLTRB(14, 11, 14, 11),
            decoration: const BoxDecoration(
              color: Sentra.bgPanel,
              border: Border(bottom: BorderSide(color: Sentra.lineGreen)),
              borderRadius: BorderRadius.vertical(top: Radius.circular(12)),
            ),
            child: Row(
              children: [
                _dot(Sentra.green.withValues(alpha: 0.55)),
                _dot(Colors.white.withValues(alpha: 0.12)),
                _dot(Colors.white.withValues(alpha: 0.12)),
                const Spacer(),
                Text('sonr --watch --map',
                    style: Sentra.mono(
                        size: 10, color: Sentra.inkFaint, spacing: 1.2)),
              ],
            ),
          ),
          // body
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 18),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final line in _log)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 7),
                    child: RichText(
                      text: TextSpan(
                        children: [
                          for (final t in line)
                            TextSpan(
                              text: t.text,
                              style: Sentra.mono(
                                  size: 11.5,
                                  color: t.color,
                                  weight: t.weight,
                                  height: 1.5),
                            ),
                        ],
                      ),
                    ),
                  ),
                Row(children: [
                  Text('_',
                      style:
                          Sentra.mono(size: 11.5, color: Sentra.green)),
                  const _Caret(),
                ]),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _dot(Color c) => Container(
        margin: const EdgeInsets.only(right: 8),
        width: 10,
        height: 10,
        decoration: BoxDecoration(shape: BoxShape.circle, color: c),
      );

  Widget _tally(String n, String label, Color color) {
    return Panel(
      padding: const EdgeInsets.symmetric(vertical: 18),
      borderColor: Sentra.lineGreen,
      child: Column(
        children: [
          Text(n, style: Sentra.display(size: 26, color: color)),
          const SizedBox(height: 6),
          Text(label,
              style: Sentra.mono(size: 9.5, color: Sentra.inkDim, spacing: 1.4)),
        ],
      ),
    );
  }
}

class _Caret extends StatefulWidget {
  const _Caret();
  @override
  State<_Caret> createState() => _CaretState();
}

class _CaretState extends State<_Caret> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1100),
  )..repeat();

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: _c.drive(_BlinkTween()),
      child: Container(width: 8, height: 15, color: Sentra.green),
    );
  }
}

/// Hard on/off blink like the site's step-end cursor.
class _BlinkTween extends Animatable<double> {
  @override
  double transform(double t) => t < 0.5 ? 1 : 0;
}
