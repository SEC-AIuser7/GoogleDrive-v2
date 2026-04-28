// =============================================================
// render.js — SVG描画 + ハイブリッドBレイアウトロジック
// data.js (window.DRIVE_DB) をDOMに描画する共通モジュール
// =============================================================

(function (global) {
  'use strict';

  // レイアウト定数 (build.py と一致させること)
  var NODE_HEIGHT = 22;
  var NODE_GAP = 8;
  var ROW_STEP = NODE_HEIGHT + NODE_GAP; // 30
  var LEVEL_X = [20, 200, 420, 650, 880, 1100];
  var LEVEL_W = [170, 200, 200, 200, 200, 200];
  var SVG_WIDTH = 1340;
  var TOP_MARGIN = 30;
  var BOTTOM_MARGIN = 30;
  var SVG_NS = 'http://www.w3.org/2000/svg';

  // 色: 階層1 はアクセントカラー、それ以外はグレー
  var COLOR_LEVEL_1 = '#8fd9c7';
  var COLOR_LEVEL_OTHER = '#8a8a8a';

  // ----------------------------------------------------------
  // ユーティリティ
  // ----------------------------------------------------------
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function escapeAttr(s) {
    return escapeHtml(s);
  }

  function setAttr(el, attrs) {
    for (var k in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, k)) {
        el.setAttribute(k, attrs[k]);
      }
    }
    return el;
  }

  function createSvgEl(tag, attrs) {
    var el = document.createElementNS(SVG_NS, tag);
    if (attrs) setAttr(el, attrs);
    return el;
  }

  // ----------------------------------------------------------
  // ハイブリッドB: 折りたたみ時の再レイアウト
  // ----------------------------------------------------------
  // ノード配列 + 折りたたみ状態 → 各ノードの y を再計算
  // collapsed: Set of folder IDs that are collapsed (子を非表示にする)
  function relayout(folders, collapsedSet) {
    var childrenMap = {};
    folders.forEach(function (f) {
      if (f.parent !== null && f.parent !== undefined) {
        if (!childrenMap[f.parent]) childrenMap[f.parent] = [];
        childrenMap[f.parent].push(f.id);
      }
    });

    var roots = folders.filter(function (f) { return f.parent === null || f.parent === undefined; });
    var newY = {};
    var hidden = {}; // 表示しないノードID → true
    var cursor = { val: TOP_MARGIN };

    function isHiddenAncestor(nodeId) {
      var f = folders[nodeId];
      var p = f.parent;
      while (p !== null && p !== undefined) {
        if (collapsedSet.has(p)) return true;
        p = folders[p].parent;
      }
      return false;
    }

    function assign(nodeId) {
      var node = folders[nodeId];
      // 自分が折りたたまれた祖先の子ならスキップ (hidden)
      if (isHiddenAncestor(nodeId)) {
        hidden[nodeId] = true;
        // y は変えない (元のレイアウトを維持してもよいが、計算からは除外)
        return null;
      }
      var kids = childrenMap[nodeId] || [];
      var visibleKids = kids.filter(function (k) { return !collapsedSet.has(nodeId); });
      // collapsedSet に nodeId 自身が含まれる → 子を一切配置しない
      if (collapsedSet.has(nodeId) || kids.length === 0) {
        // 葉として扱う
        var y = cursor.val;
        cursor.val += ROW_STEP;
        newY[nodeId] = y;
        return y;
      } else {
        var childYs = [];
        for (var i = 0; i < kids.length; i++) {
          var cy = assign(kids[i]);
          if (cy !== null) childYs.push(cy);
        }
        if (childYs.length === 0) {
          // すべての子が hidden の場合 (まれ) - 葉扱い
          var fy = cursor.val;
          cursor.val += ROW_STEP;
          newY[nodeId] = fy;
          return fy;
        }
        var midY = (Math.min.apply(null, childYs) + Math.max.apply(null, childYs)) / 2;
        newY[nodeId] = midY;
        return midY;
      }
    }

    roots.forEach(function (r) { assign(r.id); });

    var totalHeight = cursor.val + BOTTOM_MARGIN;
    return { newY: newY, hidden: hidden, totalHeight: totalHeight };
  }

  // ----------------------------------------------------------
  // SVG 構築 (初回描画)
  // ----------------------------------------------------------
  function buildSvg(drive, opts) {
    opts = opts || {};
    var svg = createSvgEl('svg', {
      id: 'tree-svg',
      width: drive.svg_width || SVG_WIDTH,
      height: drive.svg_height,
      viewBox: '0 0 ' + (drive.svg_width || SVG_WIDTH) + ' ' + drive.svg_height,
      xmlns: SVG_NS,
    });

    // 接続線グループ
    var gConn = createSvgEl('g', { id: 'connections' });
    svg.appendChild(gConn);

    var gNodes = createSvgEl('g', { id: 'nodes' });
    svg.appendChild(gNodes);

    var nodeMap = {}; // id → DOM g要素

    // ノードを生成
    drive.folders.forEach(function (f) {
      var lv = f.level;
      var x = f.layout.x;
      var y = f.layout.y;
      var w = f.layout.w;
      var fill = lv === 1 ? COLOR_LEVEL_1 : COLOR_LEVEL_OTHER;
      var fontWeight = lv === 1 ? 'bold' : 'normal';

      var emails = f.users.map(function (u) {
        var m = u.match(/^(.+?)\s*\(([^)]+)\)\s*$/);
        return m ? m[1].trim() : u.trim();
      }).join(',');

      var g = createSvgEl('g', {
        'class': 'node node-l' + lv,
        'data-id': f.id,
        'data-name': f.name,
        'data-emails': emails,
        'data-url': f.url || '',
      });
      g._folder = f; // バックリンク

      // userデータをJSONとしてdata属性で保持
      g.setAttribute('data-users', JSON.stringify(f.users.map(function (u) {
        var m = u.match(/^(.+?)\s*\(([^)]+)\)\s*$/);
        if (m) return { email: m[1].trim(), role: m[2].trim() };
        return { email: u.trim(), role: '' };
      })));

      var rect = createSvgEl('rect', {
        'class': 'node-rect',
        x: x, y: y - NODE_HEIGHT / 2 + 11 - 11 + (NODE_HEIGHT / 2 - 11),
        // y は中心座標(layout.y)を渡す想定。rect の y は中心 - height/2
        // → y_rect = layout.y - 11
        width: w, height: NODE_HEIGHT, rx: 2, ry: 2,
        fill: fill, stroke: '#000', 'stroke-width': '0.5',
      });
      // 上記の式は冗長なので簡潔に書き直し
      rect.setAttribute('y', y - NODE_HEIGHT / 2);

      var text = createSvgEl('text', {
        'class': 'node-text',
        x: x + 8,
        y: y + 4, // ベースライン調整
        fill: '#0a0a0a',
        'font-size': '11',
        'font-weight': fontWeight,
        'font-family': "'Noto Sans JP','Hiragino Sans',sans-serif",
      });
      text.textContent = f.name;

      g.appendChild(rect);
      g.appendChild(text);

      // info アイコン (ホバーでユーザー一覧表示)
      var iconG = createSvgEl('g', {
        'class': 'info-icon',
        transform: 'translate(' + (x + w + 6) + ',' + y + ')',
      });
      var circle = createSvgEl('circle', {
        r: 6, fill: 'rgba(0,0,0,0.2)', stroke: '#0a0a0a', 'stroke-width': '0.5',
      });
      var iconText = createSvgEl('text', {
        x: 0, y: 3, 'text-anchor': 'middle',
        'font-size': '9', 'font-weight': 'bold', fill: '#0a0a0a',
        'font-family': "'Noto Sans JP',sans-serif",
      });
      iconText.textContent = 'i';
      iconG.appendChild(circle);
      iconG.appendChild(iconText);
      g.appendChild(iconG);

      gNodes.appendChild(g);
      nodeMap[f.id] = g;
    });

    // 接続線を生成
    var connMap = {}; // childId → DOM path
    drive.folders.forEach(function (f) {
      if (f.parent === null || f.parent === undefined) return;
      var parent = drive.folders[f.parent];
      var px = parent.layout.x + parent.layout.w; // 親右端
      var py = parent.layout.y;
      var cx = f.layout.x; // 子左端
      var cy = f.layout.y;
      var midX = (px + cx) / 2;
      var d = 'M ' + px + ' ' + py + ' L ' + midX + ' ' + py + ' L ' + midX + ' ' + cy + ' L ' + cx + ' ' + cy;
      var path = createSvgEl('path', {
        'class': 'conn',
        d: d,
        stroke: '#444',
        'stroke-width': '1',
        fill: 'none',
        opacity: '0.6',
        'data-child-id': f.id,
      });
      gConn.appendChild(path);
      connMap[f.id] = path;
    });

    return { svg: svg, nodeMap: nodeMap, connMap: connMap };
  }

  // ----------------------------------------------------------
  // ノードの位置を更新 (折りたたみ後の再描画)
  // ----------------------------------------------------------
  function applyLayout(drive, nodeMap, connMap, layoutResult) {
    var folders = drive.folders;
    var newY = layoutResult.newY;
    var hidden = layoutResult.hidden;

    folders.forEach(function (f) {
      var g = nodeMap[f.id];
      if (!g) return;
      if (hidden[f.id]) {
        g.classList.add('hidden-node');
        return;
      }
      g.classList.remove('hidden-node');
      var y = newY[f.id];
      if (y === undefined) return;
      var rect = g.querySelector('.node-rect');
      var text = g.querySelector('.node-text');
      var iconG = g.querySelector('.info-icon');
      rect.setAttribute('y', y - NODE_HEIGHT / 2);
      text.setAttribute('y', y + 4);
      var x = f.layout.x;
      var w = f.layout.w;
      iconG.setAttribute('transform', 'translate(' + (x + w + 6) + ',' + y + ')');
    });

    // 接続線も更新
    folders.forEach(function (f) {
      var conn = connMap[f.id];
      if (!conn) return;
      if (hidden[f.id]) {
        conn.classList.add('hidden-conn');
        return;
      }
      var parent = folders[f.parent];
      // 親が hidden でないか確認
      if (hidden[parent.id]) {
        conn.classList.add('hidden-conn');
        return;
      }
      conn.classList.remove('hidden-conn');
      var px = parent.layout.x + parent.layout.w;
      var py = newY[parent.id];
      var cx = f.layout.x;
      var cy = newY[f.id];
      if (py === undefined || cy === undefined) return;
      var midX = (px + cx) / 2;
      var d = 'M ' + px + ' ' + py + ' L ' + midX + ' ' + py + ' L ' + midX + ' ' + cy + ' L ' + cx + ' ' + cy;
      conn.setAttribute('d', d);
    });

    // SVG 全体の高さも更新
    var svg = document.getElementById('tree-svg');
    if (svg) {
      svg.setAttribute('height', layoutResult.totalHeight);
      svg.setAttribute('viewBox', '0 0 ' + (drive.svg_width || SVG_WIDTH) + ' ' + layoutResult.totalHeight);
    }
  }

  // ----------------------------------------------------------
  // エクスポート
  // ----------------------------------------------------------
  global.DriveRender = {
    buildSvg: buildSvg,
    relayout: relayout,
    applyLayout: applyLayout,
    escapeHtml: escapeHtml,
    NODE_HEIGHT: NODE_HEIGHT,
    ROW_STEP: ROW_STEP,
  };

})(window);
