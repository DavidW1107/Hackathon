import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'theme/sentra_theme.dart';
import 'widgets/sentra_widgets.dart';
import 'screens/radar_screen.dart';
import 'screens/events_screen.dart';
import 'screens/station_screen.dart';
import 'services/alerts.dart';
import 'services/sensor_service.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  Alerts.init();
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.light,
    systemNavigationBarColor: Sentra.bg,
    systemNavigationBarIconBrightness: Brightness.light,
  ));
  runApp(const SentraApp());
}

class SentraApp extends StatelessWidget {
  const SentraApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'SENTRA',
      debugShowCheckedModeBanner: false,
      theme: Sentra.theme(),
      home: const AppShell(),
    );
  }
}

class AppShell extends StatefulWidget {
  const AppShell({super.key});

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _index = 0;
  final SensorService _sensor = SensorService();

  SensorEvent? _banner;
  Timer? _bannerTimer;

  @override
  void initState() {
    super.initState();
    _sensor.onMotionEvent = _showBanner;
    _sensor.start();
  }

  @override
  void dispose() {
    _bannerTimer?.cancel();
    _sensor.onMotionEvent = null;
    _sensor.dispose();
    super.dispose();
  }

  void _showBanner(SensorEvent e) {
    if (!mounted) return;
    setState(() => _banner = e);
    _bannerTimer?.cancel();
    _bannerTimer = Timer(const Duration(seconds: 5), () {
      if (mounted) setState(() => _banner = null);
    });
  }

  void _dismissBanner({bool toEvents = false}) {
    _bannerTimer?.cancel();
    setState(() {
      _banner = null;
      if (toEvents) _index = 1;
    });
  }

  @override
  Widget build(BuildContext context) {
    final screens = [
      RadarScreen(sensor: _sensor),
      EventsScreen(sensor: _sensor),
      StationScreen(sensor: _sensor),
    ];

    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: Stack(
          children: [
            Column(
              children: [
                ListenableBuilder(
                  listenable: _sensor,
                  builder: (context, _) => _TopBar(armed: _sensor.armed),
                ),
                Expanded(
                  child: IndexedStack(index: _index, children: screens),
                ),
              ],
            ),
            _MotionBanner(
              event: _banner,
              onTap: () => _dismissBanner(toEvents: true),
            ),
          ],
        ),
      ),
      bottomNavigationBar: _NavBar(
        index: _index,
        onTap: (i) => setState(() => _index = i),
      ),
    );
  }
}

/// In-app alert that slides down from the top when the station reports a
/// fresh motion event. Tap to jump to the activity feed.
class _MotionBanner extends StatefulWidget {
  const _MotionBanner({required this.event, required this.onTap});

  final SensorEvent? event;
  final VoidCallback onTap;

  @override
  State<_MotionBanner> createState() => _MotionBannerState();
}

class _MotionBannerState extends State<_MotionBanner> {
  // Retained so the card keeps its content while sliding back out.
  SensorEvent? _last;

  @override
  Widget build(BuildContext context) {
    final visible = widget.event != null;
    if (widget.event != null) _last = widget.event;
    final e = _last;
    return Positioned(
      top: 10,
      left: 16,
      right: 16,
      child: IgnorePointer(
        ignoring: !visible,
        child: AnimatedSlide(
          offset: visible ? Offset.zero : const Offset(0, -1.8),
          duration: const Duration(milliseconds: 320),
          curve: Curves.easeOutCubic,
          child: AnimatedOpacity(
            opacity: visible ? 1 : 0,
            duration: const Duration(milliseconds: 240),
            child: e == null
                ? const SizedBox.shrink()
                : GestureDetector(
                    onTap: widget.onTap,
                    child: Container(
                      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
                      decoration: BoxDecoration(
                        color: Sentra.bgPanel,
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(
                            color: Sentra.amber.withValues(alpha: 0.5)),
                        boxShadow: [
                          BoxShadow(
                            color: Colors.black.withValues(alpha: 0.55),
                            blurRadius: 18,
                            offset: const Offset(0, 6),
                          ),
                          BoxShadow(
                            color: Sentra.amber.withValues(alpha: 0.18),
                            blurRadius: 22,
                          ),
                        ],
                      ),
                      child: Row(
                        children: [
                          Container(
                            width: 36,
                            height: 36,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: Sentra.amber.withValues(alpha: 0.14),
                            ),
                            child: const Icon(Icons.directions_walk,
                                size: 18, color: Sentra.amber),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text('Movement detected',
                                    style: Sentra.sans(
                                        size: 13.5,
                                        weight: FontWeight.w600,
                                        color: Sentra.ink)),
                                const SizedBox(height: 2),
                                Text(
                                  'About ${_where(e.range)} away · tap to view',
                                  style: Sentra.sans(size: 11.5),
                                ),
                              ],
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text('now',
                              style: Sentra.mono(
                                  size: 9.5, color: Sentra.inkFaint)),
                        ],
                      ),
                    ),
                  ),
          ),
        ),
      ),
    );
  }

  static String _where(double range) => range < 1.0
      ? '${(range * 100).round()} cm'
      : '${range.toStringAsFixed(1)} m';
}

class _TopBar extends StatelessWidget {
  const _TopBar({required this.armed});

  final bool armed;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 60,
      padding: const EdgeInsets.symmetric(horizontal: 20),
      decoration: const BoxDecoration(
        color: Sentra.bg,
        border: Border(bottom: BorderSide(color: Sentra.lineGreen)),
      ),
      child: Row(
        children: [
          const BrandMark(size: 14),
          const SizedBox(width: 11),
          Text('SENTRA',
              style: Sentra.display(
                  size: 17, weight: FontWeight.w600, spacing: 2.2)),
          const Spacer(),
          StatusPill(
            label: armed ? 'Scanning' : 'Standby',
            color: armed ? Sentra.green : Sentra.inkDim,
            pulse: armed,
          ),
        ],
      ),
    );
  }
}

class _NavBar extends StatelessWidget {
  const _NavBar({required this.index, required this.onTap});

  final int index;
  final ValueChanged<int> onTap;

  static const _items = [
    (icon: Icons.radar, label: 'RADAR'),
    (icon: Icons.terminal, label: 'EVENTS'),
    (icon: Icons.dvr_outlined, label: 'STATION'),
  ];

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        color: Sentra.bgRaise,
        border: Border(top: BorderSide(color: Sentra.lineGreen)),
      ),
      child: SafeArea(
        top: false,
        child: SizedBox(
          height: 64,
          child: Row(
            children: [
              for (int i = 0; i < _items.length; i++)
                Expanded(
                  child: _NavItem(
                    icon: _items[i].icon,
                    label: _items[i].label,
                    active: i == index,
                    onTap: () => onTap(i),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _NavItem extends StatelessWidget {
  const _NavItem({
    required this.icon,
    required this.label,
    required this.active,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = active ? Sentra.green : Sentra.inkFaint;
    return InkWell(
      onTap: onTap,
      splashColor: Colors.transparent,
      highlightColor: Colors.transparent,
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
            decoration: active
                ? BoxDecoration(
                    shape: BoxShape.circle,
                    boxShadow: [
                      BoxShadow(
                        color: Sentra.green.withValues(alpha: 0.5),
                        blurRadius: 14,
                      ),
                    ],
                  )
                : null,
            child: Icon(icon, size: 21, color: color),
          ),
          const SizedBox(height: 6),
          Text(label,
              style: Sentra.mono(size: 9, color: color, spacing: 1.4)),
        ],
      ),
    );
  }
}
