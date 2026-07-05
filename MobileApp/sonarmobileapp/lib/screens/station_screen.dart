import 'package:flutter/material.dart';
import '../services/sensor_service.dart';
import '../theme/sentra_theme.dart';
import '../widgets/sentra_widgets.dart';

class StationScreen extends StatefulWidget {
  const StationScreen({super.key, required this.sensor});

  final SensorService sensor;

  @override
  State<StationScreen> createState() => _StationScreenState();
}

class _StationScreenState extends State<StationScreen> {
  bool _petSafe = true;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: widget.sensor,
      builder: (context, _) => _body(context, widget.sensor),
    );
  }

  Widget _body(BuildContext context, SensorService sensor) {
    final armed = sensor.armed;
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 28),
      children: [
        const Kicker('Station'),
        const SizedBox(height: 10),
        Text('This device', style: Sentra.display(size: 30, height: 1.05)),
        const SizedBox(height: 20),

        // arm card
        Panel(
          color: Sentra.bgPanel,
          borderColor:
              armed ? Sentra.lineGreenMid : Sentra.lineWhite,
          glow: armed,
          padding: const EdgeInsets.all(22),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    width: 44,
                    height: 44,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: Sentra.lineGreenMid),
                      color: Sentra.green.withValues(alpha: 0.06),
                    ),
                    child: const Icon(Icons.laptop_mac,
                        color: Sentra.green, size: 20),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(sensor.deviceName,
                            style: Sentra.sans(
                                size: 14.5,
                                weight: FontWeight.w600,
                                color: Sentra.ink)),
                        const SizedBox(height: 3),
                        Text('sonr-station-a4 · ${sensor.host}',
                            style: Sentra.mono(
                                size: 10.5, color: Sentra.inkFaint)),
                      ],
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.edit_outlined,
                        size: 16, color: Sentra.inkDim),
                    tooltip: 'Edit station name & address',
                    onPressed: () => _editStation(context, sensor),
                  ),
                ],
              ),
              const SizedBox(height: 20),
              Container(
                padding: const EdgeInsets.fromLTRB(16, 14, 12, 14),
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(11),
                  color: Sentra.bg,
                  border: Border.all(
                    color: armed
                        ? Sentra.green.withValues(alpha: 0.35)
                        : Sentra.lineWhite,
                  ),
                ),
                child: Row(
                  children: [
                    StatusPill(
                      label: armed ? 'Armed · sweeping' : 'Standby',
                      color: armed ? Sentra.green : Sentra.inkDim,
                      pulse: armed,
                    ),
                    const Spacer(),
                    Switch.adaptive(
                      value: armed,
                      onChanged: sensor.setArmed,
                      activeColor: Sentra.onGreen,
                      activeTrackColor: Sentra.green,
                      inactiveThumbColor: Sentra.inkDim,
                      inactiveTrackColor: Sentra.bgRaise,
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 28),

        // settings
        const Kicker('Detection'),
        const SizedBox(height: 12),
        Panel(
          padding: EdgeInsets.zero,
          borderColor: Sentra.lineWhite,
          child: Column(
            children: [
              _toggleRow(
                icon: Icons.pets,
                title: 'Pet-safe mode',
                subtitle: 'Caps output power & duty cycle',
                value: _petSafe,
                onChanged: (v) => setState(() => _petSafe = v),
              ),
              _rowDivider(),
              _toggleRow(
                icon: Icons.notifications_active_outlined,
                title: 'Push alerts',
                subtitle: 'Notify this phone on detection',
                value: sensor.pushAlerts,
                onChanged: sensor.setPushAlerts,
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        Center(
          child: Text('Air-gapped by design · nothing to the cloud',
              style: Sentra.mono(size: 10, color: Sentra.inkFaint, spacing: 1)),
        ),
      ],
    );
  }

  Future<void> _editStation(BuildContext context, SensorService sensor) async {
    final nameCtrl = TextEditingController(text: sensor.deviceName);
    final hostCtrl = TextEditingController(text: sensor.host);
    final saved = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Sentra.bgPanel,
        title: Text('Edit station',
            style: Sentra.sans(
                size: 16, weight: FontWeight.w600, color: Sentra.ink)),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: nameCtrl,
              autofocus: true,
              textCapitalization: TextCapitalization.words,
              style: Sentra.sans(size: 14, color: Sentra.ink),
              decoration: InputDecoration(
                labelText: 'Name',
                labelStyle: Sentra.sans(size: 12, color: Sentra.inkFaint),
                hintText: 'MacBook Pro · Room A4',
                hintStyle: Sentra.sans(size: 14, color: Sentra.inkFaint),
              ),
            ),
            const SizedBox(height: 14),
            TextField(
              controller: hostCtrl,
              keyboardType: TextInputType.url,
              style: Sentra.mono(size: 13, color: Sentra.ink),
              decoration: InputDecoration(
                labelText: 'Address',
                labelStyle: Sentra.sans(size: 12, color: Sentra.inkFaint),
                hintText: '192.168.1.42:8765',
                hintStyle: Sentra.mono(size: 13, color: Sentra.inkFaint),
                helperText:
                    'host:port of sensor.py — localhost on this machine, '
                    'the laptop\'s LAN IP from a phone',
                helperStyle: Sentra.sans(size: 11, color: Sentra.inkFaint),
              ),
              onSubmitted: (_) => Navigator.pop(ctx, true),
            ),
          ],
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Cancel')),
          TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Save')),
        ],
      ),
    );
    if (saved == true) {
      sensor.setDeviceName(nameCtrl.text);
      sensor.setHost(hostCtrl.text);
    }
  }

  Widget _rowDivider() =>
      Container(height: 1, color: Sentra.lineWhite);

  Widget _toggleRow({
    required IconData icon,
    required String title,
    required String subtitle,
    required bool value,
    required ValueChanged<bool> onChanged,
  }) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 14, 10, 14),
      child: Row(
        children: [
          Icon(icon,
              size: 18,
              color: value ? Sentra.green : Sentra.inkFaint),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title,
                    style: Sentra.sans(
                        size: 14,
                        weight: FontWeight.w500,
                        color: Sentra.ink)),
                const SizedBox(height: 2),
                Text(subtitle,
                    style: Sentra.sans(size: 11.5, color: Sentra.inkFaint)),
              ],
            ),
          ),
          Switch.adaptive(
            value: value,
            onChanged: onChanged,
            activeColor: Sentra.onGreen,
            activeTrackColor: Sentra.green,
            inactiveThumbColor: Sentra.inkDim,
            inactiveTrackColor: Sentra.bgRaise,
          ),
        ],
      ),
    );
  }
}
