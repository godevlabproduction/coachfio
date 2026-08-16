---
name: Aetheric Utility
colors:
  surface: '#f9f9f8'
  surface-dim: '#dadad9'
  surface-bright: '#f9f9f8'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f3f4f3'
  surface-container: '#eeeeed'
  surface-container-high: '#e8e8e7'
  surface-container-highest: '#e2e2e2'
  on-surface: '#1a1c1c'
  on-surface-variant: '#454652'
  inverse-surface: '#2f3130'
  inverse-on-surface: '#f1f1f0'
  outline: '#767684'
  outline-variant: '#c6c5d5'
  surface-tint: '#4854bb'
  primary: '#4450b7'
  on-primary: '#ffffff'
  primary-container: '#5e6ad2'
  on-primary-container: '#fdfaff'
  inverse-primary: '#bdc2ff'
  secondary: '#5f5e5e'
  on-secondary: '#ffffff'
  secondary-container: '#e2dfde'
  on-secondary-container: '#636262'
  tertiary: '#834f00'
  on-tertiary: '#ffffff'
  tertiary-container: '#a56500'
  on-tertiary-container: '#fffaf8'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#dfe0ff'
  primary-fixed-dim: '#bdc2ff'
  on-primary-fixed: '#000965'
  on-primary-fixed-variant: '#2e3aa2'
  secondary-fixed: '#e5e2e1'
  secondary-fixed-dim: '#c8c6c5'
  on-secondary-fixed: '#1c1b1b'
  on-secondary-fixed-variant: '#474746'
  tertiary-fixed: '#ffddbb'
  tertiary-fixed-dim: '#ffb867'
  on-tertiary-fixed: '#2b1700'
  on-tertiary-fixed-variant: '#673d00'
  background: '#f9f9f8'
  on-background: '#1a1c1c'
  surface-variant: '#e2e2e2'
  surface-raised: '#FFFFFF'
  border-subtle: rgba(0, 0, 0, 0.08)
  dark-bg: '#08090A'
  dark-surface: '#171717'
  dark-border: rgba(255, 255, 255, 0.1)
typography:
  headline-xl:
    fontFamily: Inter
    fontSize: 40px
    fontWeight: '600'
    lineHeight: 48px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '500'
    lineHeight: 32px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-mono:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.05em
  headline-lg-mobile:
    fontFamily: Inter
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 34px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  base: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 40px
  gutter: 24px
  margin-mobile: 16px
  margin-desktop: 64px
---

## Brand & Style

The design system prioritizes intellectual calm and mechanical precision over typical gaming aesthetics. It is designed for high-performance players who view gameplay as a craft to be mastered. The visual language is "utility-first," drawing heavy influence from sophisticated productivity tools like Linear and Claude. 

The style is **Modern Minimalist with a focus on Tonal Depth**. It uses expansive whitespace, a restrained monochromatic foundation, and high-readability typography to create an environment that feels like a professional workspace. Every visual element exists to facilitate focus, reducing cognitive load during complex gameplay analysis.

## Colors

The palette is rooted in a "paper and ink" philosophy. The primary background is a warm off-white (#F9F9F8) to reduce eye strain, while the dark mode utilizes a deep near-black (#08090A). 

- **Primary:** A sophisticated Indigo (#5E6AD2) is the sole chromatic actor, reserved strictly for primary calls to action and critical status indicators.
- **Neutrals:** Grayscale values are derived from the background hue to ensure harmony.
- **Usage:** Maintain a high signal-to-noise ratio by using the primary accent sparingly. Layouts should be dominated by neutral tones and subtle border definitions.

## Typography

Typography is the primary driver of hierarchy. **Inter** provides a neutral, highly legible foundation for all interface elements. **JetBrains Mono** is introduced for secondary metadata, timestamps, and technical gameplay data to reinforce the "analytical tool" persona.

Tighten letter-spacing on headlines to create a more "engineered" look. Ensure body text maintains generous line-height to support long-form coaching feedback. All labels and technical data should be uppercase when using the monospaced font.

## Layout & Spacing

This design system employs a **Fixed-Fluid hybrid grid**. Content is contained within a maximum width of 1280px for readability, centered on the screen. 

- **Grid:** A 12-column system is used for desktop, collapsing to 4 columns for mobile.
- **Rhythm:** An 8px linear scale dictates all padding and margins. 
- **Density:** Use "Lush" spacing for marketing and onboarding pages (xl units) and "Compact" spacing (md units) for dashboard and analysis views to maximize information density without clutter.

## Elevation & Depth

Depth is communicated through **Tonal Layering** and **Subtle Outlines** rather than traditional shadows. 

- **Surface Levels:** The background is the lowest level. Cards and containers sit one level above, distinguished by a 1px border (#000000 at 8% opacity) and a slightly different fill (pure #FFFFFF).
- **Interactions:** On hover, elements should not "lift" with shadows. Instead, the border color should darken slightly, or the background fill should shift by a fraction of a percent.
- **Overlays:** Modals and dropdowns use a very soft, large-radius ambient shadow (0px 10px 30px rgba(0,0,0,0.04)) to separate them from the main canvas.

## Shapes

The shape language is "Soft-Square." Using a `0.25rem` (4px) base radius ensures the UI feels approachable but retains its professional, structured edge. This radius applies to buttons, input fields, and small cards. Larger containers like main content areas may use the `rounded-lg` (8px) variant to soften the overall layout.

## Components

- **Buttons:** Primary buttons use a solid Indigo fill with white text. Secondary buttons use a subtle gray-wash fill or a 1px border with no fill. Hover states involve a slight darkening of the background color (approx 5%).
- **Inputs:** Fields are defined by a 1px light-gray border. On focus, the border transitions to the primary Indigo color with a 2px outer glow (0% blur) of the same color at 10% opacity.
- **Cards:** Used for game selection and data modules. They feature a 1px subtle border and no shadow. The header of the card should use a slightly darker background tint to separate title from content.
- **Chips/Badges:** Small, rounded-sm elements with low-saturation backgrounds (e.g., light gray or very pale indigo) used to denote game genres or coach specialties.
- **Iconography:** Use 1.5pt stroke icons. Avoid filled icons unless used as a status indicator.