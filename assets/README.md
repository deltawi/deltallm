# DeltaLLM Assets

This directory contains visual assets for the DeltaLLM project.

## Files

### Logo
- `logo.svg` - Main project logo (vector)
- `logo.png` - Main project logo (1024x1024 PNG)
- `logo-small.png` - Small logo for favicon (256x256 PNG)

### Demo GIFs/Videos
- `demo-dashboard.gif` - Dashboard walkthrough (30 seconds)
- `demo-api.gif` - API usage demo (15 seconds)
- `demo-cli.gif` - CLI usage demo (15 seconds)

### Screenshots
- `screenshot-dashboard-overview.png` - Dashboard overview page
- `screenshot-organizations.png` - Organizations management
- `screenshot-api-keys.png` - API key management
- `screenshot-budget.png` - Budget tracking

### Social
- `social-preview.png` - GitHub repository social preview (1200x630)
- `twitter-card.png` - Twitter/OG card image

### Architecture
- `architecture.svg` - System architecture diagram
- `architecture-dark.svg` - Dark mode version

## Creating Demo GIFs

### Dashboard Demo (Recommended Tools)
1. **Screen Studio** (macOS) - Beautiful automatic zoom
2. **ScreenFlow** (macOS) - Professional editing
3. **OBS Studio** (All platforms) - Free, record then convert
4. **Loom** (All platforms) - Easy sharing, download as MP4
5. **CleanShot X** (macOS) - Built-in GIF export

### Recording Tips
- Resolution: 1440x900 or 1920x1080
- Frame rate: 15-30 fps for GIFs
- Duration: 15-30 seconds
- Highlight key features with annotations
- Show realistic data (not lorem ipsum)

### Converting to GIF
```bash
# Using ffmpeg
ffmpeg -i demo.mp4 -vf "fps=15,scale=1200:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer" demo.gif

# Or use online tools:
# - https://ezgif.com/video-to-gif
# - https://cloudconvert.com/mp4-to-gif
```

## Brand Guidelines

### Colors
- Primary: `#3B82F6` (Blue)
- Secondary: `#8B5CF6` (Purple)
- Accent: `#10B981` (Green)
- Dark: `#1F2937` (Gray 800)
- Light: `#F9FAFB` (Gray 50)

### Typography
- Headings: Inter or system-ui
- Code: JetBrains Mono or Fira Code

## TODO

- [ ] Create logo.svg
- [ ] Create demo-dashboard.gif
- [ ] Create demo-api.gif
- [ ] Create social-preview.png
- [ ] Create architecture diagram
