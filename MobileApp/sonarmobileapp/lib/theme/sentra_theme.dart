import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// SENTRA design tokens — mirrors the landing-page palette and type ramp
/// (deep-green on near-black, `#22e06b` accent, IBM Plex + Space Grotesk).
class Sentra {
  Sentra._();

  // ---- surfaces ----
  static const bg = Color(0xFF070A09);
  static const bgRaise = Color(0xFF0B0F0D);
  static const bgPanel = Color(0xFF0E1512);
  static const terminal = Color(0xFF060908);

  // ---- ink ----
  static const ink = Color(0xFFE6F2EA);
  static const inkMuted = Color(0xFF9FB3A8);
  static const inkDim = Color(0xFF8AA396);
  static const inkFaint = Color(0xFF5C7266);

  // ---- accents ----
  static const green = Color(0xFF22E06B);
  static const greenBright = Color(0xFF5BFFA0);
  static const greenSoft = Color(0xFF8DFFB0);
  static const amber = Color(0xFFFFD166);
  static const onGreen = Color(0xFF04060A);

  // ---- lines (rgba(52,226,122, a) / white a) ----
  static const lineGreen = Color(0x1F34E27A); // .12
  static const lineGreenMid = Color(0x4734E27A); // .28
  static const lineGreenHot = Color(0x8034E27A); // .50
  static const lineWhite = Color(0x14FFFFFF); // .08

  // ---------------------------------------------------------------- type
  static TextStyle display({
    double size = 20,
    FontWeight weight = FontWeight.w600,
    Color color = ink,
    double spacing = -0.4,
    double height = 1.1,
  }) => GoogleFonts.spaceGrotesk(
    fontSize: size,
    fontWeight: weight,
    color: color,
    letterSpacing: spacing,
    height: height,
  );

  static TextStyle sans({
    double size = 14,
    FontWeight weight = FontWeight.w400,
    Color color = inkMuted,
    double spacing = 0,
    double height = 1.5,
  }) => GoogleFonts.ibmPlexSans(
    fontSize: size,
    fontWeight: weight,
    color: color,
    letterSpacing: spacing,
    height: height,
  );

  static TextStyle mono({
    double size = 12,
    FontWeight weight = FontWeight.w500,
    Color color = ink,
    double spacing = 0,
    double height = 1.4,
  }) => GoogleFonts.ibmPlexMono(
    fontSize: size,
    fontWeight: weight,
    color: color,
    letterSpacing: spacing,
    height: height,
  );

  // ---------------------------------------------------------------- theme
  static ThemeData theme() {
    final base = ThemeData.dark(useMaterial3: true);
    return base.copyWith(
      scaffoldBackgroundColor: bg,
      canvasColor: bg,
      colorScheme: base.colorScheme.copyWith(
        primary: green,
        secondary: greenBright,
        surface: bgPanel,
        onPrimary: onGreen,
      ),
      splashColor: green.withValues(alpha: 0.08),
      highlightColor: green.withValues(alpha: 0.05),
      textTheme: GoogleFonts.ibmPlexSansTextTheme(base.textTheme).apply(
        bodyColor: ink,
        displayColor: ink,
      ),
    );
  }
}
