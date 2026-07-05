// Basic smoke test: the app builds and shows the SENTRA shell.

import 'package:flutter_test/flutter_test.dart';

import 'package:sonarmobileapp/main.dart';

void main() {
  testWidgets('App builds and shows the top bar', (WidgetTester tester) async {
    await tester.pumpWidget(const SentraApp());

    expect(find.text('SENTRA'), findsOneWidget);
  });
}
