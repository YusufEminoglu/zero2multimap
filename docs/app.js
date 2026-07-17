// 02Multimap Landing Page Application Logic

document.addEventListener('DOMContentLoaded', () => {
    const gridContainer = document.getElementById('interactive-grid');
    const coordXText = document.getElementById('coord-x');
    const coordYText = document.getElementById('coord-y');
    const layoutButtons = document.querySelectorAll('.btn-demo');

    // Panel themes configuration
    const themes = [
        { name: 'Base Layer (OSM Light)', class: 'map-osm', desc: 'Sync Active Layers' },
        { name: 'Population Density 2026', class: 'map-pop', desc: 'Focus Layer' },
        { name: 'Seismic Risk Zonation', class: 'map-seismic', desc: 'Map Theme' },
        { name: 'Urban Heat Island Indices', class: 'map-heat', desc: 'Map Theme' },
        { name: 'Zoning & Land Use Policies', class: 'map-zoning', desc: 'Focus Layer' },
        { name: 'Transport Hub Service Areas', class: 'map-transport', desc: 'Focus Layer' },
        { name: 'Topographic Slopes & Contours', class: 'map-topo', desc: 'Sync Active Layers' },
        { name: 'Flood Inundation Extent', class: 'map-flood', desc: 'Map Theme' }
    ];

    // Current grid size
    let currentGrid = '2x2';

    // Initialize grid
    function buildGrid(layout) {
        gridContainer.className = `demo-grid grid-${layout}`;
        gridContainer.innerHTML = '';
        
        let count = 4;
        if (layout === '1x2') count = 2;
        if (layout === '2x4') count = 8;

        for (let i = 0; i < count; i++) {
            const theme = themes[i % themes.length];
            
            const panel = document.createElement('div');
            panel.className = 'demo-panel';
            panel.dataset.index = i;

            const header = document.createElement('div');
            header.className = 'demo-panel-header';
            header.innerHTML = `
                <span>Panel ${i + 1}</span>
                <span style="color: #6b7280; font-size: 0.65rem;">${theme.desc}</span>
            `;

            const canvas = document.createElement('div');
            canvas.className = 'demo-canvas';

            const mapIllustration = document.createElement('div');
            mapIllustration.className = `demo-map ${theme.class}`;

            const laser = document.createElement('div');
            laser.className = 'laser-pointer';

            canvas.appendChild(mapIllustration);
            canvas.appendChild(laser);
            panel.appendChild(header);
            panel.appendChild(canvas);
            gridContainer.appendChild(panel);

            // Bind mouse events to canvas for crosshair tracking
            canvas.addEventListener('mousemove', (e) => handleMouseMove(e, i));
            canvas.addEventListener('mouseleave', () => handleMouseLeave(i));
        }

        // Add CSS rules for demo map backgrounds dynamically if not already added
        if (!document.getElementById('demo-map-styles')) {
            const style = document.createElement('style');
            style.id = 'demo-map-styles';
            style.innerHTML = `
                .map-osm { background: radial-gradient(circle, rgba(0, 122, 204, 0.15) 10%, transparent 80%), repeating-linear-gradient(0deg, #1b1b22, #1b1b22 1px, transparent 1px, transparent 15px), repeating-linear-gradient(90deg, #1b1b22, #1b1b22 1px, transparent 1px, transparent 15px); }
                .map-pop { background: radial-gradient(circle at 40% 50%, rgba(155, 89, 182, 0.4) 0%, transparent 60%), repeating-linear-gradient(45deg, #1b1b22, #1b1b22 1px, transparent 1px, transparent 20px); }
                .map-seismic { background: radial-gradient(circle at 70% 30%, rgba(231, 76, 60, 0.4) 0%, transparent 55%), repeating-linear-gradient(135deg, #1b1b22, #1b1b22 1px, transparent 1px, transparent 20px); }
                .map-heat { background: radial-gradient(circle at 50% 60%, rgba(243, 156, 18, 0.4) 0%, transparent 60%), repeating-linear-gradient(90deg, #1b1b22, #1b1b22 2px, transparent 2px, transparent 25px); }
                .map-zoning { background: radial-gradient(circle at 30% 70%, rgba(46, 204, 113, 0.35) 0%, transparent 55%), repeating-linear-gradient(0deg, #181822, #181822 4px, transparent 4px, transparent 30px); }
                .map-transport { background: radial-gradient(circle at 80% 80%, rgba(52, 152, 219, 0.4) 0%, transparent 50%), repeating-linear-gradient(90deg, #1b1b22, #1b1b22 1px, transparent 1px, transparent 18px); }
                .map-topo { background: radial-gradient(circle at 20% 20%, rgba(149, 165, 166, 0.3) 0%, transparent 65%), repeating-linear-gradient(60deg, #1b1b22, #1b1b22 2px, transparent 2px, transparent 22px); }
                .map-flood { background: radial-gradient(circle at 60% 40%, rgba(41, 128, 185, 0.45) 0%, transparent 70%), repeating-linear-gradient(120deg, #1b1b22, #1b1b22 3px, transparent 3px, transparent 28px); }
            `;
            document.head.appendChild(style);
        }
    }

    // Handles mouse movements on active canvas and translates it to other canvases
    function handleMouseMove(event, sourceIdx) {
        const canvas = event.currentTarget;
        const rect = canvas.getBoundingClientRect();
        const x = event.clientX - rect.left;
        const y = event.clientY - rect.top;
        
        // Calculate relative coordinates percentage
        const pctX = (x / rect.width) * 100;
        const pctY = (y / rect.height) * 100;

        // Display relative geographic-style coordinates in header
        const mapX = (pctX * 3500 + 400000).toFixed(2);
        const mapY = ((100 - pctY) * 3500 + 4200000).toFixed(2);
        coordXText.innerText = mapX;
        coordYText.innerText = mapY;

        const panels = gridContainer.querySelectorAll('.demo-panel');
        panels.forEach((panel) => {
            const panelIdx = parseInt(panel.dataset.index);
            const laser = panel.querySelector('.laser-pointer');
            
            if (panelIdx === sourceIdx) {
                // Active panel gets standard highlight border, pointer hidden
                panel.classList.add('active-panel');
                laser.style.display = 'none';
            } else {
                // Synchronized panels show crosshair tracking the relative point
                panel.classList.remove('active-panel');
                laser.style.left = `${pctX}%`;
                laser.style.top = `${pctY}%`;
                laser.style.display = 'block';
            }
        });
    }

    // Hide crosshairs and remove highlights when mouse leaves grid container
    function handleMouseLeave(sourceIdx) {
        coordXText.innerText = '0.00';
        coordYText.innerText = '0.00';

        const panels = gridContainer.querySelectorAll('.demo-panel');
        panels.forEach((panel) => {
            panel.classList.remove('active-panel');
            const laser = panel.querySelector('.laser-pointer');
            laser.style.display = 'none';
        });
    }

    // Layout selector controls
    layoutButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            layoutButtons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            const gridType = btn.dataset.grid;
            currentGrid = gridType;
            buildGrid(gridType);
        });
    });

    // Initial setup
    buildGrid(currentGrid);
});
