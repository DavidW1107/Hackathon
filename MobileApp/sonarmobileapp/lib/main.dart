import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'theme/sentra_theme.dart';
import 'widgets/sentra_widgets.dart';
import 'screens/radar_screen.dart';
import 'screens/events_screen.dart';
import 'screens/station_screen.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
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
  bool _armed = true;

  @override
  Widget build(BuildContext context) {
    final screens = [
      RadarScreen(armed: _armed),
      const EventsScreen(),
      StationScreen(
        armed: _armed,
        onArmedChanged: (v) => setState(() => _armed = v),
      ),
    ];

    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: Column(
          children: [
            _TopBar(armed: _armed),
            Expanded(
              child: IndexedStack(index: _index, children: screens),
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
            label: armed ? 'Live sweep' : 'Standby',
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
