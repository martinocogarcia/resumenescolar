from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import queue
import re
import sys
import threading
import time
import traceback
import unicodedata
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse


APP_TITLE = "Resumen Escolar"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765
SCHOOLNET_URL = "https://schoolnet.colegium.com/webapp/es_CL/login"
CLASSROOM_URL = "https://classroom.google.com/"
CLASSROOM_4A_COURSE_ID = "ODQ5Nzk2MDk0NDk5"
CLASSROOM_4A_COURSE_URL = f"https://classroom.google.com/c/{CLASSROOM_4A_COURSE_ID}"
CLASSROOM_4A_CLASSWORK_URL = f"https://classroom.google.com/w/{CLASSROOM_4A_COURSE_ID}/t/all"
CHROME_EXE = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
EDGE_EXE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
LOCAL_SITE_PACKAGES = RUNTIME_DIR / "site-packages"
APP_TEMP_DIR = RUNTIME_DIR / "temp"
OUTBOX_DIR = PROJECT_ROOT / "outbox"
EVIDENCE_CACHE_DIR = RUNTIME_DIR / "evidence_cache"
EVIDENCE_STORE_PATH = EVIDENCE_CACHE_DIR / "evidence_store.json"
TEXT_LIMIT = 120000
MEANINGFUL_TEXT_MIN_CHARS = 80
STUDENT_NAME = "Gabito Garc\u00eda"
EVIDENCE_STORE_VERSION = 1
INCREMENTAL_KNOWN_STOP_COUNT = 3
ATTRIBUTE_TEXT_SCRIPT = """
() => Array.from(document.querySelectorAll('[aria-label], [title], input[placeholder], textarea[placeholder]'))
  .slice(0, 800)
  .map((el) => [
    el.getAttribute('aria-label'),
    el.getAttribute('title'),
    el.getAttribute('placeholder'),
    el.value,
  ].filter(Boolean).join(' '))
  .filter(Boolean)
  .join('\\n')
"""
CLICK_TEXT_SCRIPT = """
(terms) => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const wanted = terms.map(normalize).filter(Boolean);
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const selectors = [
    'a',
    'button',
    '[role="button"]',
    '[role="link"]',
    '[role="tab"]',
    '[role="menuitem"]',
    '[onclick]',
    '[tabindex]:not([tabindex="-1"])'
  ].join(',');
  const candidates = Array.from(document.querySelectorAll(selectors));
  for (const term of wanted) {
    for (const el of candidates) {
      if (!isVisible(el)) continue;
      const label = normalize([
        el.innerText,
        el.textContent,
        el.getAttribute('aria-label'),
        el.getAttribute('title'),
        el.getAttribute('href')
      ].filter(Boolean).join(' '));
      if (!label.includes(term)) continue;
      el.scrollIntoView({ block: 'center', inline: 'center' });
      el.click();
      return {
        clicked: true,
        term,
        text: (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 140),
        href: el.getAttribute('href') || '',
        tag: el.tagName
      };
    }
  }
  return { clicked: false };
}
"""
CLASSROOM_ATTACHMENT_LINKS_SCRIPT = """
() => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const textOf = (el) => [
    el.innerText,
    el.textContent,
    el.getAttribute('aria-label'),
    el.getAttribute('title'),
    el.getAttribute('href')
  ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const candidates = [];
  for (const el of Array.from(document.querySelectorAll('a[href]'))) {
    if (!isVisible(el)) continue;
    const href = el.getAttribute('href') || '';
    const label = textOf(el);
    const haystack = normalize(`${label} ${href}`);
    const looksPdf = haystack.includes('.pdf') || haystack.includes(' pdf');
    const looksDriveAttachment = (
      haystack.includes('drive.google') ||
      haystack.includes('docs.google') ||
      haystack.includes('attachment') ||
      haystack.includes('archivo adjunto') ||
      haystack.includes('material')
    );
    if (!looksPdf && !looksDriveAttachment) continue;
    candidates.push({
      label: label.slice(0, 260),
      href,
      is_pdf: looksPdf,
    });
  }
  const seen = new Set();
  return candidates.filter((candidate) => {
    const key = `${candidate.label}|${candidate.href}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 12);
}
"""
CLASSROOM_STREAM_POSTS_SCRIPT = """
(terms) => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const clean = (value) => (value || '').replace(/\\r\\n/g, '\\n').replace(/\\r/g, '\\n')
    .split('\\n')
    .map((line) => line.replace(/\\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\\n');
  const textOf = (el) => clean([
    el.innerText,
    el.getAttribute('aria-label'),
    el.getAttribute('title')
  ].filter(Boolean).join('\\n'));
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const wanted = terms.map(normalize).filter(Boolean);
  const hasKeyword = (text) => {
    const normalized = normalize(text);
    return wanted.some((term) => normalized.includes(term));
  };
  const looksLikeDateLine = (line) => {
    const normalized = normalize(line);
    return (
      /\\b\\d{1,2}[/-]\\d{1,2}(?:[/-]\\d{2,4})?\\b/.test(normalized) ||
      /\\b\\d{1,2}\\s+de\\s+[a-z]+\\b/.test(normalized) ||
      /\\b(hoy|ayer|manana|publicado|publico|posted)\\b/.test(normalized)
    );
  };
  const bestPostRoot = (el) => {
    let current = el;
    let best = null;
    for (let depth = 0; current && current !== document.body && depth < 8; depth += 1) {
      if (isVisible(current)) {
        const text = textOf(current);
        if (text.length >= 80 && text.length <= 3400 && hasKeyword(text)) {
          best = current;
          const role = normalize(current.getAttribute('role'));
          const label = normalize(current.getAttribute('aria-label'));
          if (role === 'article' || role === 'listitem' || label.includes('public')) break;
        }
      }
      current = current.parentElement;
    }
    return best || el;
  };
  const candidates = [];
  const nodes = Array.from(document.querySelectorAll('article, [role="article"], [role="listitem"], p, span, div, a'));
  for (const el of nodes) {
    if (!isVisible(el)) continue;
    const seedText = textOf(el);
    if (!seedText || seedText.length > 2400 || !hasKeyword(seedText)) continue;
    const root = bestPostRoot(el);
    const text = textOf(root);
    if (!text || text.length < 50 || !hasKeyword(text)) continue;
    const lines = text.split('\\n').map((line) => line.trim()).filter(Boolean);
    const dateLine = lines.find(looksLikeDateLine) || '';
    const authorLine = lines.find((line) => line.length <= 90 && !looksLikeDateLine(line) && !hasKeyword(line)) || '';
    const links = Array.from(root.querySelectorAll('a[href]'))
      .filter(isVisible)
      .map((link) => ({
        label: textOf(link).replace(/\\n/g, ' ').slice(0, 220),
        href: link.getAttribute('href') || '',
      }))
      .filter((link) => link.href || link.label);
    candidates.push({
      author: authorLine,
      posted: dateLine,
      text: text.slice(0, 2600),
      links,
    });
  }
  const seen = new Set();
  return candidates.filter((candidate) => {
    const key = normalize(candidate.text);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 10);
}
"""
CLASSROOM_RELEVANT_POST_CANDIDATES_SCRIPT = """
(terms) => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const clean = (value) => (value || '').replace(/\\r\\n/g, '\\n').replace(/\\r/g, '\\n')
    .split('\\n')
    .map((line) => line.replace(/\\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\\n');
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const textOf = (el) => clean([
    el.innerText || el.textContent,
    el.getAttribute('aria-label'),
    el.getAttribute('title')
  ].filter(Boolean).join('\\n'));
  const wanted = terms.map(normalize).filter(Boolean);
  const hasKeyword = (text) => {
    const normalized = normalize(text);
    return wanted.some((term) => normalized.includes(term));
  };
  const looksDate = (line) => {
    const normalized = normalize(line);
    return (
      /\\b(publicado|posted|fecha limite|due|vence|ayer|hoy|manana)\\b/.test(normalized) ||
      /\\b\\d{1,2}[/-]\\d{1,2}(?:[/-]\\d{2,4})?\\b/.test(normalized) ||
      /\\b\\d{1,2}\\s+(?:de\\s+)?[a-z]{3,}\\b/.test(normalized)
    );
  };
  const looksPostedLine = (line) => {
    const normalized = normalize(line);
    return /\\b(publicado|posted|fecha limite|due|vence|ayer|hoy|manana)\\b/.test(normalized);
  };
  const looksNoise = (line) => {
    const normalized = normalize(line);
    return (
      normalized === 'ver material' ||
      normalized === 'material' ||
      normalized === 'book' ||
      normalized === 'comentarios de la clase' ||
      normalized === 'opciones' ||
      normalized === 'tareas pendientes' ||
      normalized === 'pending assignments' ||
      normalized === 'sin tareas pendientes' ||
      normalized === 'trabajo de clase' ||
      normalized === 'ver todo' ||
      normalized === 'tu trabajo' ||
      normalized === 'ver tu trabajo' ||
      normalized.includes('no tienes que entregar') ||
      normalized.includes('agregar comentario') ||
      normalized.includes('class comments')
    );
  };
  const subjectTerms = [
    'matematica',
    'matematicas',
    'lenguaje',
    'comunicacion',
    'historia',
    'ciencias',
    'ingles',
    'religion',
    'artes',
    'musica',
    'computacion',
    'tecnologia',
    'educacion fisica',
    'orientacion'
  ];
  const looksSubject = (line) => {
    const normalized = normalize(line);
    return line.length <= 90 && subjectTerms.some((term) => normalized.includes(term));
  };
  const subjectFrom = (root, lines, title) => {
    const titleIndex = lines.indexOf(title);
    if (titleIndex > 0) {
      const previousSubject = lines.slice(0, titleIndex).reverse().find(looksSubject);
      if (previousSubject) return previousSubject;
    }
    let current = root;
    for (let depth = 0; current && current !== document.body && depth < 6; depth += 1) {
      let sibling = current.previousElementSibling;
      for (let attempts = 0; sibling && attempts < 5; attempts += 1) {
        if (isVisible(sibling)) {
          const siblingLines = textOf(sibling).split('\\n').map((line) => line.trim()).filter(Boolean);
          const subject = siblingLines.reverse().find(looksSubject);
          if (subject) return subject;
        }
        sibling = sibling.previousElementSibling;
      }
      current = current.parentElement;
    }
    return '';
  };
  const relevantTitleCount = (text) => {
    const titles = new Set();
    for (const line of text.split('\\n').map((item) => item.trim()).filter(Boolean)) {
      if (line.length <= 180 && hasKeyword(line) && !looksPostedLine(line) && !looksNoise(line)) {
        titles.add(normalize(line));
      }
    }
    return titles.size;
  };
  const subjectCount = (text) => {
    const subjects = new Set();
    for (const line of text.split('\\n').map((item) => item.trim()).filter(Boolean)) {
      if (looksSubject(line)) subjects.add(normalize(line));
    }
    return subjects.size;
  };
  const looksGlobalRoot = (text) => {
    const normalized = normalize(text);
    const first = normalize(text.split('\\n').map((line) => line.trim()).filter(Boolean).slice(0, 6).join(' '));
    return (
      first.includes('filtro por tema') ||
      first.includes('todos los temas') ||
      first.includes('ocultar todo') ||
      first.includes('trabajo de clase') ||
      normalized.includes('cursos en los que te has inscrito') ||
      (text.length > 900 && relevantTitleCount(text) > 2) ||
      (text.length > 1200 && subjectCount(text) > 1)
    );
  };
  const pickTitle = (text) => {
    const lines = text.split('\\n').map((line) => line.trim()).filter(Boolean);
    return lines.find((line) => line.length <= 180 && hasKeyword(line) && !looksPostedLine(line) && !looksNoise(line)) || '';
  };
  const bestRoot = (el) => {
    let current = el;
    let best = el;
    for (let depth = 0; current && current !== document.body && depth < 9; depth += 1) {
      if (isVisible(current)) {
        const text = textOf(current);
        if (text.length >= 40 && text.length <= 4200 && hasKeyword(text) && !looksGlobalRoot(text)) best = current;
        const role = normalize(current.getAttribute('role'));
        const label = normalize(current.getAttribute('aria-label'));
        if (role === 'article' || role === 'listitem' || label.includes('material') || label.includes('public')) break;
      }
      current = current.parentElement;
    }
    return best;
  };
  const clickableSelector = [
    'a[href]',
    '[role="button"]',
    '[role="link"]',
    '[tabindex]:not([tabindex="-1"])',
    '[onclick]'
  ].join(',');
  const selector = [
    'article',
    '[role="article"]',
    '[role="listitem"]',
    '[data-course-stream-item-id]',
    '[data-material-id]',
    clickableSelector
  ].join(',');
  const findClickTarget = (root, seed, title) => {
    const titleNorm = normalize(title);
    const seedIsClickable = seed.matches && seed.matches(clickableSelector);
    const clickables = []
      .concat(seedIsClickable ? [seed] : [])
      .concat(Array.from(root.querySelectorAll(clickableSelector)))
      .filter(isVisible)
      .filter((candidate) => {
        const label = normalize(textOf(candidate));
        return (
          label &&
          label !== 'opciones' &&
          label !== 'more' &&
          label !== 'mas' &&
          !label.includes('comentario') &&
          !label.includes('comment')
        );
      });
    const matchingClickable = clickables.find((candidate) => {
      const label = normalize(textOf(candidate));
      return label.includes(titleNorm) || hasKeyword(label);
    });
    return matchingClickable || root || seed;
  };
  const detailHrefFrom = (root, seed, clickTarget, title) => {
    const hrefs = [];
    const addHref = (el) => {
      if (!el || !el.getAttribute) return;
      const href = el.getAttribute('href') || '';
      if (href) hrefs.push(href);
    };
    addHref(seed);
    addHref(clickTarget);
    for (const link of Array.from(root.querySelectorAll('a[href]')).filter(isVisible)) {
      addHref(link);
    }
    const rootRect = root.getBoundingClientRect();
    const seedRect = seed.getBoundingClientRect();
    const titleNorm = normalize(title);
    const nearbyLinks = Array.from(document.querySelectorAll('a[href]')).filter(isVisible).filter((link) => {
      const href = link.getAttribute('href') || '';
      const normalizedHref = normalize(href);
      if (!normalizedHref.includes('/m/') && !normalizedHref.includes('/a/') && !normalizedHref.includes('/details')) return false;
      const label = normalize(textOf(link));
      if (titleNorm && label.includes(titleNorm)) return true;
      const rect = link.getBoundingClientRect();
      const sameBand = Math.abs((rect.top + rect.bottom) / 2 - (seedRect.top + seedRect.bottom) / 2) <= 90;
      const nearCard = rect.bottom >= rootRect.top - 20 && rect.top <= rootRect.bottom + 20;
      return sameBand || nearCard;
    });
    for (const link of nearbyLinks) addHref(link);
    return hrefs.find((href) => {
      const normalized = normalize(href);
      return (
        normalized.includes('/details') ||
        normalized.includes('/m/') ||
        normalized.includes('/a/')
      );
    }) || '';
  };
  const candidates = [];
  let counter = 0;
  for (const el of Array.from(document.querySelectorAll(selector))) {
    if (!isVisible(el)) continue;
    const label = textOf(el);
    if (!label || !hasKeyword(label)) continue;
    const root = bestRoot(el);
    const fullText = textOf(root);
    const isolated = !looksGlobalRoot(fullText);
    const title = pickTitle(label) || pickTitle(fullText);
    if (!title || !hasKeyword(title)) continue;
    const lines = fullText.split('\\n').map((line) => line.trim()).filter(Boolean);
    const subject = subjectFrom(root, lines, title);
    const posted = lines.find((line) => looksPostedLine(line)) ||
      lines.find((line) => line !== title && looksDate(line)) ||
      '';
    const contentLines = lines.filter((line) => line !== posted && line !== title && !looksNoise(line));
    const links = Array.from(root.querySelectorAll('a[href]'))
      .filter(isVisible)
      .map((link) => ({
        label: textOf(link).replace(/\\n/g, ' ').slice(0, 220),
        href: link.getAttribute('href') || ''
      }))
      .filter((link) => link.label || link.href);
    const token = `resumen-escolar-post-${counter}`;
    counter += 1;
    const clickTarget = findClickTarget(root, el, title);
    const detailHref = detailHrefFrom(root, el, clickTarget, title);
    clickTarget.setAttribute('data-resumen-escolar-post-token', token);
    candidates.push({
      token,
      title: title.slice(0, 220),
      subject: subject.slice(0, 120),
      posted: posted.slice(0, 140),
      preview: contentLines.join('\\n').slice(0, 3600),
      href: el.getAttribute('href') || '',
      detail_href: detailHref,
      isolated,
      root_length: fullText.length,
      root_title_count: relevantTitleCount(fullText),
      root_subject_count: subjectCount(fullText),
      click_target_text: textOf(clickTarget).replace(/\\n/g, ' ').slice(0, 220),
      click_target_tag: clickTarget.tagName,
      links
    });
  }
  const seen = new Set();
  return candidates.filter((candidate) => {
    const key = normalize(`${candidate.title}|${candidate.posted}|${candidate.detail_href}|${candidate.href}`);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 20);
}
"""
CLASSROOM_CLICK_POST_CANDIDATE_SCRIPT = """
(token) => {
  const el = document.querySelector(`[data-resumen-escolar-post-token="${token}"]`);
  if (!el) return { clicked: false };
  el.scrollIntoView({ block: 'center', inline: 'center' });
  el.click();
  return { clicked: true };
}
"""
CLASSROOM_OPEN_POST_BY_TITLE_SCRIPT = """
(targetTitle) => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const textOf = (el) => clean([
    el.innerText || el.textContent,
    el.getAttribute('aria-label'),
    el.getAttribute('title')
  ].filter(Boolean).join(' '));
  const target = normalize(targetTitle);
  if (!target) return { clicked: false };
  const seeds = Array.from(document.querySelectorAll('a, button, [role], [tabindex], span, div'))
    .filter(isVisible)
    .map((el) => ({ el, label: textOf(el), rect: el.getBoundingClientRect() }))
    .filter((item) => {
      const label = normalize(item.label);
      return label && label.includes(target) && !label.includes('opciones de');
    })
    .sort((a, b) => a.label.length - b.label.length || a.rect.top - b.rect.top);
  for (const seed of seeds.slice(0, 10)) {
    const targets = [];
    const add = (el) => {
      if (el && !targets.includes(el) && isVisible(el)) targets.push(el);
    };
    add(seed.el.closest('a[href]'));
    add(seed.el.closest('[role="link"]'));
    add(seed.el.closest('[role="button"]'));
    add(seed.el.closest('button'));
    let current = seed.el;
    for (let depth = 0; current && current !== document.body && depth < 8; depth += 1) {
      const role = normalize(current.getAttribute('role'));
      const tabIndex = current.getAttribute('tabindex');
      const cursor = window.getComputedStyle(current).cursor;
      if (
        current.getAttribute('onclick') ||
        tabIndex !== null ||
        role === 'button' ||
        role === 'link' ||
        cursor === 'pointer'
      ) {
        add(current);
      }
      current = current.parentElement;
    }
    add(seed.el);
    for (const targetEl of targets) {
      try {
        targetEl.scrollIntoView({ block: 'center', inline: 'center' });
        targetEl.click();
        const href = targetEl.getAttribute ? (targetEl.getAttribute('href') || '') : '';
        return { clicked: true, label: textOf(targetEl).slice(0, 240), tag: targetEl.tagName, href };
      } catch (_error) {}
    }
  }
  return { clicked: false };
}
"""
CLASSROOM_DETAIL_BY_TITLE_SCRIPT = """
(targetTitle) => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const clean = (value) => (value || '').replace(/\\r\\n/g, '\\n').replace(/\\r/g, '\\n')
    .split('\\n')
    .map((line) => line.replace(/\\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\\n');
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const textOf = (el) => clean([
    el.innerText || el.textContent,
    el.getAttribute('aria-label'),
    el.getAttribute('title')
  ].filter(Boolean).join('\\n'));
  const target = normalize(targetTitle);
  if (!target) return null;
  const looksNoise = (line) => {
    const normalized = normalize(line);
    return (
      normalized === 'material' ||
      normalized === 'book' ||
      normalized === 'assignment' ||
      normalized === 'tarea' ||
      normalized === 'ver material' ||
      normalized === 'view material' ||
      normalized === 'opciones' ||
      normalized === 'more' ||
      normalized === 'mas' ||
      normalized === 'comment' ||
      normalized === 'mas opciones' ||
      /^\\d+$/.test(normalized) ||
      /^\\d+\\s+comentarios?$/.test(normalized) ||
      /^\\d+\\s+comments?$/.test(normalized) ||
      normalized.includes('opciones de material') ||
      normalized.includes('opciones de tarea') ||
      normalized.includes('no tienes que entregar')
    );
  };
  const isStopLine = (line) => {
    const normalized = normalize(line);
    return (
      normalized.includes('comentarios de la clase') ||
      normalized.includes('class comments') ||
      normalized.includes('anade un comentario') ||
      normalized.includes('add class comment') ||
      normalized.includes('comentario privado') ||
      normalized.includes('private comment')
    );
  };
  const looksPosted = (line) => /\\b(publicado|posted)\\b/.test(normalize(line));
  const looksDue = (line) => /\\b(fecha de entrega|due|vence)\\b/.test(normalize(line));
  const subjectTerms = [
    'matematica',
    'matematicas',
    'lenguaje',
    'comunicacion',
    'historia',
    'ciencias',
    'ingles',
    'religion',
    'artes',
    'musica',
    'computacion',
    'tecnologia',
    'educacion fisica',
    'orientacion'
  ];
  const looksSubject = (line) => {
    const normalized = normalize(line);
    return line.length <= 90 && subjectTerms.some((term) => normalized.includes(term));
  };
  const detailUrl = (href) => {
    if (!href) return '';
    try {
      const absolute = new URL(href, window.location.href).href;
      const pathParts = new URL(absolute).pathname.split('/').filter(Boolean);
      if (
        pathParts.length === 5 &&
        pathParts[0] === 'c' &&
        (pathParts[2] === 'm' || pathParts[2] === 'a') &&
        pathParts[4] === 'details'
      ) {
        return absolute;
      }
    } catch (_error) {}
    return '';
  };
  const bodyFromLines = (lines, title, posted, due) => {
    const titleNorm = normalize(title);
    const postedNorm = normalize(posted);
    const dueNorm = normalize(due);
    const output = [];
    let started = false;
    for (const line of lines) {
      const normalized = normalize(line);
      if (!normalized) continue;
      if (isStopLine(line)) break;
      if (titleNorm && normalized === titleNorm) {
        started = true;
        continue;
      }
      if (postedNorm && normalized === postedNorm) {
        started = true;
        continue;
      }
      if (dueNorm && normalized === dueNorm) {
        started = true;
        continue;
      }
      if (looksPosted(line) || looksDue(line)) {
        started = true;
        continue;
      }
      if (!started) continue;
      if (looksNoise(line)) continue;
      if (titleNorm && normalized.includes(titleNorm) && line.length <= title.length + 40) continue;
      output.push(line);
    }
    return clean(output.join('\\n'));
  };
  const seeds = Array.from(document.querySelectorAll('a, button, [role], [tabindex], span, div'))
    .filter(isVisible)
    .map((el) => ({ el, text: textOf(el), rect: el.getBoundingClientRect() }))
    .filter((item) => normalize(item.text).includes(target) && !normalize(item.text).includes('opciones de'))
    .sort((a, b) => a.text.length - b.text.length || a.rect.top - b.rect.top);
  for (const seed of seeds.slice(0, 12)) {
    let current = seed.el;
    for (let depth = 0; current && current !== document.body && depth < 12; depth += 1) {
      if (!isVisible(current)) {
        current = current.parentElement;
        continue;
      }
      const fullText = textOf(current);
      const normalizedFull = normalize(fullText);
      if (!normalizedFull.includes(target)) {
        current = current.parentElement;
        continue;
      }
      if (fullText.length > 6500) break;
      const lines = fullText.split('\\n').map((line) => line.trim()).filter(Boolean);
      const title = lines.find((line) => normalize(line).includes(target) && line.length <= 260) || targetTitle;
      const posted = lines.find(looksPosted) || '';
      const due = lines.find(looksDue) || '';
      const bodyText = bodyFromLines(lines, title, posted, due);
      const links = Array.from(current.querySelectorAll('a[href]'))
        .filter(isVisible)
        .map((link) => {
          const href = link.getAttribute('href') || '';
          let absolute = href;
          try { absolute = new URL(href, window.location.href).href; } catch (_error) {}
          return {
            label: textOf(link).replace(/\\n/g, ' ').slice(0, 220),
            href,
            absolute_href: absolute,
          };
        })
        .filter((link) => link.label || link.href || link.absolute_href);
      const detail_href = links.map((link) => detailUrl(link.href) || detailUrl(link.absolute_href)).find(Boolean) || '';
      const subject = lines.slice(0, 12).find(looksSubject) || '';
      if (bodyText.length >= 80 || detail_href) {
        return {
          title,
          subject,
          posted,
          due,
          body_text: bodyText.slice(0, 4200),
          preview: bodyText.slice(0, 4200),
          detail_href,
          links,
          isolated: true,
          source_method: 'title_dom',
          root_length: fullText.length,
        };
      }
      current = current.parentElement;
    }
  }
  return null;
}
"""
CLASSROOM_STRUCTURED_TOPIC_CARDS_SCRIPT = """
(terms) => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const clean = (value) => (value || '').replace(/\\r\\n/g, '\\n').replace(/\\r/g, '\\n')
    .split('\\n')
    .map((line) => line.replace(/\\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\\n');
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const textOf = (el) => clean([
    el.innerText || el.textContent,
    el.getAttribute('aria-label'),
    el.getAttribute('title')
  ].filter(Boolean).join('\\n'));
  const wanted = terms.map(normalize).filter(Boolean);
  const hasKeyword = (text) => {
    const normalized = normalize(text);
    return wanted.some((term) => normalized.includes(term));
  };
  const subjectTerms = [
    'matematica',
    'matematicas',
    'lenguaje',
    'comunicacion',
    'historia',
    'ciencias',
    'ingles',
    'religion',
    'artes',
    'musica',
    'computacion',
    'tecnologia',
    'educacion fisica',
    'orientacion'
  ];
  const topicHeading = (() => {
    const headings = Array.from(document.querySelectorAll('main h1, h1, [role="heading"]'))
      .filter(isVisible)
      .map(textOf)
      .filter((text) => text && text.length <= 120);
    return headings.find((text) => {
      const normalized = normalize(text);
      return subjectTerms.some((term) => normalized.includes(term));
    }) || '';
  })();
  const looksNoise = (line) => {
    const normalized = normalize(line);
    return (
      normalized === 'material' ||
      normalized === 'book' ||
      normalized === 'assignment' ||
      normalized === 'tarea' ||
      normalized === 'ver material' ||
      normalized === 'view material' ||
      normalized === 'ver tarea' ||
      normalized === 'view assignment' ||
      normalized === 'opciones' ||
      normalized === 'more' ||
      normalized === 'mas' ||
      normalized === 'comment' ||
      normalized === 'mas opciones' ||
      /^\\d+$/.test(normalized) ||
      /^\\d+\\s+comentarios?$/.test(normalized) ||
      /^\\d+\\s+comments?$/.test(normalized) ||
      normalized === 'comentarios de la clase' ||
      normalized === 'class comments' ||
      normalized.includes('anade un comentario') ||
      normalized.includes('add class comment') ||
      normalized.includes('no tienes que entregar') ||
      normalized.includes('nothing to turn in')
    );
  };
  const isStopLine = (line) => {
    const normalized = normalize(line);
    return (
      normalized.includes('comentarios de la clase') ||
      normalized.includes('class comments') ||
      normalized.includes('anade un comentario') ||
      normalized.includes('add class comment') ||
      normalized.includes('comentario privado') ||
      normalized.includes('private comment')
    );
  };
  const looksPosted = (line) => /\\b(publicado|posted)\\b/.test(normalize(line));
  const looksDue = (line) => /\\b(fecha de entrega|due|vence)\\b/.test(normalize(line));
  const detailUrl = (href) => {
    if (!href) return '';
    try {
      const absolute = new URL(href, window.location.href).href;
      const pathParts = new URL(absolute).pathname.split('/').filter(Boolean);
      if (
        pathParts.length === 5 &&
        pathParts[0] === 'c' &&
        (pathParts[2] === 'm' || pathParts[2] === 'a') &&
        pathParts[4] === 'details'
      ) {
        return absolute;
      }
      return '';
    } catch (_error) {
      return '';
    }
  };
  const parseAnchor = (card) => {
    const anchors = Array.from(card.querySelectorAll('a[href], a[aria-label]')).filter(isVisible);
    for (const anchor of anchors) {
      const label = clean(anchor.getAttribute('aria-label') || textOf(anchor)).replace(/\\n/g, ' ');
      const match = label.match(/^(Material|Tarea|Assignment|Pregunta|Question):\\s*["']?(.+?)["']?$/i);
      const href = anchor.getAttribute('href') || '';
      if (match && match[2]) {
        return {
          kind: match[1],
          title: match[2].replace(/^["']+|["']+$/g, '').trim(),
          href,
          detail_href: detailUrl(href)
        };
      }
      if (detailUrl(href) && label && label.length <= 260 && hasKeyword(label)) {
        return { kind: '', title: label, href, detail_href: detailUrl(href) };
      }
    }
    return { kind: '', title: '', href: '', detail_href: '' };
  };
  const pickLineTitle = (lines) => {
    return lines.find((line) => line.length <= 220 && hasKeyword(line) && !looksPosted(line) && !looksDue(line) && !looksNoise(line)) || '';
  };
  const bodyFromLines = (lines, title, posted, due) => {
    const titleNorm = normalize(title);
    const postedNorm = normalize(posted);
    const dueNorm = normalize(due);
    const output = [];
    let started = false;
    for (const line of lines) {
      const normalized = normalize(line);
      if (!normalized) continue;
      if (isStopLine(line)) break;
      if (titleNorm && normalized === titleNorm) {
        started = true;
        continue;
      }
      if (postedNorm && normalized === postedNorm) {
        started = true;
        continue;
      }
      if (dueNorm && normalized === dueNorm) {
        started = true;
        continue;
      }
      if (looksPosted(line) || looksDue(line)) {
        started = true;
        continue;
      }
      if (!started) continue;
      if (looksNoise(line)) continue;
      if (titleNorm && normalized.includes(titleNorm) && line.length <= title.length + 40) continue;
      output.push(line);
    }
    return clean(output.join('\\n'));
  };
  const cards = Array.from(document.querySelectorAll('[data-stream-item-id][data-stream-item-type]'))
    .filter(isVisible)
    .filter((card) => !card.parentElement || !card.parentElement.closest('[data-stream-item-id][data-stream-item-type]'));
  const results = [];
  for (const card of cards) {
    const fullText = textOf(card);
    if (!fullText) continue;
    const lines = fullText.split('\\n').map((line) => line.trim()).filter(Boolean);
    const anchorInfo = parseAnchor(card);
    const title = anchorInfo.title || pickLineTitle(lines);
    if (!title || !hasKeyword(title)) continue;
    const posted = lines.find(looksPosted) || '';
    const due = lines.find(looksDue) || '';
    const bodyText = bodyFromLines(lines, title, posted, due);
    const links = Array.from(card.querySelectorAll('a[href]'))
      .filter(isVisible)
      .map((link) => {
        const href = link.getAttribute('href') || '';
        let absolute = href;
        try { absolute = new URL(href, window.location.href).href; } catch (_error) {}
        return {
          label: textOf(link).replace(/\\n/g, ' ').slice(0, 220),
          href,
          absolute_href: absolute,
        };
      })
      .filter((link) => link.label || link.href || link.absolute_href);
    results.push({
      token: '',
      title: title.slice(0, 220),
      kind: anchorInfo.kind || '',
      subject: topicHeading.slice(0, 120),
      posted: posted.slice(0, 140),
      due: due.slice(0, 140),
      preview: bodyText.slice(0, 4200),
      body_text: bodyText.slice(0, 4200),
      href: anchorInfo.href || '',
      detail_href: anchorInfo.detail_href || '',
      links,
      isolated: true,
      is_structured_card: true,
      source_method: 'card_dom',
      data_stream_item_id: card.getAttribute('data-stream-item-id') || '',
      data_stream_item_type: card.getAttribute('data-stream-item-type') || '',
      root_length: fullText.length,
      root_title_count: 1,
      root_subject_count: topicHeading ? 1 : 0,
    });
  }
  const seen = new Set();
  return results.filter((candidate) => {
    const key = normalize(`${candidate.title}|${candidate.posted}|${candidate.detail_href}|${candidate.data_stream_item_id}`);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 20);
}
"""
CLASSROOM_VISIBLE_RELEVANT_BLOCKS_SCRIPT = """
(terms) => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const clean = (value) => (value || '').replace(/\\r\\n/g, '\\n').replace(/\\r/g, '\\n')
    .split('\\n')
    .map((line) => line.replace(/\\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\\n');
  const textOf = (el) => clean([
    el.innerText || el.textContent,
    el.getAttribute('aria-label'),
    el.getAttribute('title')
  ].filter(Boolean).join('\\n'));
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const wanted = terms.map(normalize).filter(Boolean);
  const hasKeyword = (text) => {
    const normalized = normalize(text);
    return wanted.some((term) => normalized.includes(term));
  };
  const looksPostedLine = (line) => {
    const normalized = normalize(line);
    return /\\b(publicado|posted|fecha limite|due|vence|ayer|hoy|manana)\\b/.test(normalized);
  };
  const looksNoise = (line) => {
    const normalized = normalize(line);
    return (
      normalized === 'material' ||
      normalized === 'book' ||
      normalized === 'ver material' ||
      normalized === 'comentarios de la clase' ||
      normalized === 'opciones' ||
      normalized === 'tareas pendientes' ||
      normalized === 'pending assignments' ||
      normalized === 'sin tareas pendientes' ||
      normalized === 'trabajo de clase' ||
      normalized === 'ver todo' ||
      normalized === 'tu trabajo' ||
      normalized === 'ver tu trabajo' ||
      normalized.includes('no tienes que entregar') ||
      normalized.includes('agregar comentario') ||
      normalized.includes('class comments')
    );
  };
  const subjectTerms = [
    'matematica',
    'matematicas',
    'lenguaje',
    'comunicacion',
    'historia',
    'ciencias',
    'ingles',
    'religion',
    'artes',
    'musica',
    'computacion',
    'tecnologia',
    'educacion fisica',
    'orientacion'
  ];
  const looksSubject = (line) => {
    const normalized = normalize(line);
    return line.length <= 90 && subjectTerms.some((term) => normalized.includes(term));
  };
  const titleLine = (text) => {
    const lines = text.split('\\n').map((line) => line.trim()).filter(Boolean);
    return lines.find((line) => line.length <= 220 && hasKeyword(line) && !looksPostedLine(line) && !looksNoise(line)) || '';
  };
  const detailLines = (text, title, posted) => {
    const titleNorm = normalize(title);
    const postedNorm = normalize(posted);
    return text.split('\\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .filter((line) => {
        const normalized = normalize(line);
        if (!normalized || looksNoise(line)) return false;
        if (titleNorm && normalized === titleNorm) return false;
        if (postedNorm && normalized === postedNorm) return false;
        return true;
      });
  };
  const selectors = [
    'article',
    '[role="article"]',
    '[role="listitem"]',
    '[data-material-id]',
    '[data-course-stream-item-id]',
    '[data-item-id]',
    'main div'
  ].join(',');
  const raw = [];
  for (const el of Array.from(document.querySelectorAll(selectors))) {
    if (!isVisible(el)) continue;
    const text = textOf(el);
    if (!text || text.length < 120 || text.length > 6000 || !hasKeyword(text)) continue;
    const title = titleLine(text);
    if (!title) continue;
    const lines = text.split('\\n').map((line) => line.trim()).filter(Boolean);
    const titleIndex = lines.indexOf(title);
    const subject = titleIndex > 0
      ? (lines.slice(0, titleIndex).reverse().find(looksSubject) || '')
      : '';
    const posted = lines.find((line) => line !== title && looksPostedLine(line)) || '';
    const detail = detailLines(text, title, posted).join('\\n');
    if (detail.length < 120) continue;
    const rect = el.getBoundingClientRect();
    const links = Array.from(el.querySelectorAll('a[href]'))
      .filter(isVisible)
      .map((link) => ({
        label: textOf(link).replace(/\\n/g, ' ').slice(0, 220),
        href: link.getAttribute('href') || ''
      }))
      .filter((link) => link.label || link.href);
    raw.push({
      title: title.slice(0, 220),
      subject: subject.slice(0, 120),
      posted: posted.slice(0, 160),
      text: detail.slice(0, 4200),
      links,
      area: Math.round(rect.width * rect.height),
      length: text.length,
    });
  }
  raw.sort((a, b) => a.length - b.length || a.area - b.area);
  const seen = new Set();
  return raw.filter((candidate) => {
    const key = normalize(`${candidate.title}|${candidate.posted}`);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 10);
}
"""
CLASSROOM_TOPIC_LINKS_SCRIPT = """
() => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const clean = (value) => (value || '').replace(/\\r\\n/g, '\\n').replace(/\\r/g, '\\n')
    .split('\\n')
    .map((line) => line.replace(/\\s+/g, ' ').trim())
    .filter(Boolean)
    .join(' ');
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const labelOf = (el) => clean(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '');
  const isGeneric = (label) => {
    const normalized = normalize(label);
    return !normalized ||
      normalized === 'todos los temas' ||
      normalized === 'all topics' ||
      normalized === 'trabajo de clase' ||
      normalized === 'classwork' ||
      normalized === 'ver todo' ||
      normalized === 'opciones';
  };
  const topicTerms = [
    'matem',
    'matematica',
    'matematicas',
    'ingl',
    'ingles',
    'leng',
    'lenguaje',
    'comunic',
    'comunicacion',
    'ciencias sociales',
    'ciencias naturales',
    'historia',
    'relig',
    'religion',
    'artes',
    'musi',
    'musica',
    'computacion',
    'tecnolog',
    'tecnologia',
    'educacion fisica',
    'fisica',
    'orientacion'
  ];
  const looksTopicLabel = (label) => {
    const normalized = normalize(label);
    return label.length <= 120 && topicTerms.some((term) => normalized.includes(term));
  };
  const results = [];
  for (const link of Array.from(document.querySelectorAll('a[href]'))) {
    if (!isVisible(link)) continue;
    const href = link.getAttribute('href') || '';
    let absolute = '';
    try {
      absolute = new URL(href, window.location.href).href;
    } catch (_error) {
      absolute = href;
    }
    if (!absolute.includes('classroom.google.') || !absolute.includes('/tc/')) continue;
    const label = labelOf(link);
    if (isGeneric(label) || !looksTopicLabel(label)) continue;
    results.push({ label: label.slice(0, 140), href, absolute_href: absolute });
  }
  const seen = new Set();
  return results.filter((candidate) => {
    const key = normalize(candidate.absolute_href);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 20);
}
"""
CLASSROOM_TOPIC_OPTIONS_SCRIPT = """
() => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const labelOf = (el) => clean(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '');
  const isGeneric = (label) => {
    const normalized = normalize(label);
    return !normalized ||
      normalized === 'todos los temas' ||
      normalized === 'all topics' ||
      normalized === 'trabajo de clase' ||
      normalized === 'classwork' ||
      normalized === 'ver todo' ||
      normalized === 'opciones' ||
      normalized.includes('filtro por tema') ||
      normalized.includes('filter by topic');
  };
  const topicTerms = [
    'matem',
    'matematica',
    'matematicas',
    'ingl',
    'ingles',
    'leng',
    'lenguaje',
    'comunic',
    'comunicacion',
    'ciencias sociales',
    'ciencias naturales',
    'historia',
    'relig',
    'religion',
    'artes',
    'musi',
    'musica',
    'computacion',
    'tecnolog',
    'tecnologia',
    'educacion fisica',
    'fisica',
    'orientacion'
  ];
  const looksTopicLabel = (label) => {
    const normalized = normalize(label);
    return label.length <= 120 && topicTerms.some((term) => normalized.includes(term));
  };
  const selectors = [
    '[role="option"]',
    '[role="menuitem"]',
    '[role="menuitemradio"]',
    '[role="listbox"] [role]',
    '[aria-selected]',
    'li',
    'div[role]',
    '[data-value]'
  ].join(',');
  const seen = new Set();
  const out = [];
  const addLabel = (rawLabel) => {
    const label = clean(String(rawLabel || '').replace(/^tema\\s+/i, '').replace(/^topic\\s+/i, ''));
    const pieces = label
      .split(/\\n|\\s{2,}|(?=MATEM)|(?=INGL)|(?=CIENCIAS)|(?=COMPUT)|(?=MUSI)|(?=ARTES)|(?=RELIG)/i)
      .map(clean)
      .filter(Boolean);
    for (const piece of pieces.length ? pieces : [label]) {
      const normalized = normalize(piece);
      if (
        piece.length <= 140 &&
        !isGeneric(piece) &&
        looksTopicLabel(piece) &&
        !seen.has(normalized)
      ) {
        seen.add(normalized);
        out.push(piece);
      }
    }
  };
  for (const el of Array.from(document.querySelectorAll(selectors)).filter(isVisible)) {
    addLabel(labelOf(el));
    addLabel(el.getAttribute('aria-label') || '');
    addLabel(el.getAttribute('data-value') || '');
  }
  return out.slice(0, 20);
}
"""
CLASSROOM_OPEN_TOPIC_MENU_SCRIPT = """
() => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const labelOf = (el) => clean([
    el.innerText || el.textContent,
    el.getAttribute('aria-label'),
    el.getAttribute('title'),
    el.getAttribute('placeholder')
  ].filter(Boolean).join(' '));
  const wanted = [
    'filtro por tema',
    'filter by topic',
    'todos los temas',
    'all topics',
    'trabajo de clase',
    'classwork'
  ];
  const controlSelector = [
    'button',
    '[role="button"]',
    '[role="combobox"]',
    '[aria-haspopup]',
    '[tabindex]:not([tabindex="-1"])'
  ].join(',');
  const candidates = Array.from(document.querySelectorAll(controlSelector))
    .filter(isVisible)
    .map((el) => ({ el, label: labelOf(el), rect: el.getBoundingClientRect() }))
    .filter((item) => {
      const normalized = normalize(item.label);
      if (!normalized) return false;
      if (normalized.includes('calendar') || normalized.includes('gemini')) return false;
      if (wanted.some((term) => normalized.includes(term))) return true;
      return (
        item.el.getAttribute('role') === 'combobox' &&
        item.el.getAttribute('aria-haspopup') === 'listbox'
      );
    })
    .sort((a, b) => {
      const aExact = wanted.some((term) => normalize(a.label) === term) ? 0 : 1;
      const bExact = wanted.some((term) => normalize(b.label) === term) ? 0 : 1;
      return aExact - bExact || a.rect.top - b.rect.top || a.rect.left - b.rect.left;
    });
  const target = candidates[0];
  if (!target) return { clicked: false };
  target.el.scrollIntoView({ block: 'center', inline: 'center' });
  target.el.click();
  return { clicked: true, label: target.label };
}
"""
CLASSROOM_CLICK_TOPIC_OPTION_SCRIPT = """
(targetLabel) => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const labelOf = (el) => clean(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '');
  const target = normalize(targetLabel);
  if (!target) return { clicked: false };
  const selectors = [
    '[role="option"]',
    '[role="menuitem"]',
    '[role="menuitemradio"]',
    '[role="listbox"] [role]',
    '[aria-selected]',
    'li',
    'div[role]',
    '[data-value]'
  ].join(',');
  const candidates = Array.from(document.querySelectorAll(selectors))
    .filter(isVisible)
    .map((el) => ({ el, label: labelOf(el) }))
    .filter((item) => normalize(item.label).includes(target))
    .sort((a, b) => {
      const aExact = normalize(a.label) === target ? 0 : 1;
      const bExact = normalize(b.label) === target ? 0 : 1;
      return aExact - bExact || a.label.length - b.label.length;
    });
  const match = candidates.length ? candidates[0].el : null;
  if (!match) return { clicked: false };
  match.scrollIntoView({ block: 'center', inline: 'center' });
  match.click();
  return { clicked: true, label: labelOf(match) };
}
"""
CLASSROOM_EXPAND_VISIBLE_ITEMS_SCRIPT = """
() => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const textOf = (el) => [
    el.innerText || el.textContent,
    el.getAttribute('aria-label'),
    el.getAttribute('title')
  ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  let clicked = 0;
  const clickIfSafe = (el) => {
    if (!isVisible(el)) return false;
    const label = normalize(textOf(el));
    if (
      label.includes('ocultar todo') ||
      label.includes('hide all') ||
      label.includes('ver tu trabajo') ||
      label.includes('view your work') ||
      label.includes('filtro') ||
      label.includes('filter') ||
      label.includes('todos los temas') ||
      label.includes('opciones')
    ) return false;
    const shouldClick = (
      label.includes('mostrar todo') ||
      label.includes('show all') ||
      label.includes('expand all') ||
      label.includes('ver mas') ||
      label.includes('show more') ||
      el.getAttribute('aria-expanded') === 'false'
    );
    if (!shouldClick) return false;
    try {
      el.scrollIntoView({ block: 'center', inline: 'center' });
      el.click();
      clicked += 1;
      return true;
    } catch (error) {
      return false;
    }
  };
  const candidates = Array.from(document.querySelectorAll('button, [role="button"], [aria-expanded="false"]'));
  for (const el of candidates.slice(0, 80)) {
    if (clicked >= 12) break;
    clickIfSafe(el);
  }
  return { clicked };
}
"""
CLASSROOM_SCROLL_DETAIL_SCRIPT = """
() => {
  const isScrollable = (el) => el && el.scrollHeight > el.clientHeight + 20;
  const candidates = [document.scrollingElement, document.documentElement, document.body]
    .concat(Array.from(document.querySelectorAll('main, [role="main"], [role="dialog"], [aria-modal="true"], div')));
  let moved = 0;
  for (const el of candidates) {
    if (!isScrollable(el)) continue;
    const before = el.scrollTop;
    el.scrollTop = Math.min(el.scrollTop + Math.max(360, el.clientHeight * 0.85), el.scrollHeight);
    if (el.scrollTop !== before) moved += 1;
  }
  return { moved };
}
"""
SCHOOLNET_EXPAND_GRADE_ROWS_SCRIPT = """
() => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const subjects = [
    'lenguaje y comunicacion',
    'idioma extranjero ingles',
    'matematica',
    'historia geografia y ciencias sociales',
    'ciencias naturales',
    'artes visuales',
    'musica',
    'tecnologia',
    'educacion fisica y salud',
    'religion',
  ];
  let clicked = 0;
  const nodes = Array.from(document.querySelectorAll('body *')).filter(isVisible).map((el) => {
    const rect = el.getBoundingClientRect();
    const text = textOf(el);
    const label = [
      text,
      el.getAttribute('aria-label'),
      el.getAttribute('title'),
      el.getAttribute('class'),
      el.getAttribute('aria-expanded'),
    ].filter(Boolean).join(' ');
    return { el, text, label, norm: normalize(text), labelNorm: normalize(label), rect, cy: rect.top + rect.height / 2 };
  }).filter((node) => node.labelNorm && node.label.length <= 260);

  const clickNearSubject = (subjectNode) => {
    const controls = nodes.filter((node) => {
      if (node.el === subjectNode.el) return false;
      const nearSameRow = Math.abs(node.cy - subjectNode.cy) <= 12;
      const leftOfSubject = node.rect.left <= subjectNode.rect.left + 10;
      const looksExpandable = (
        node.labelNorm.includes('false') ||
        node.labelNorm.includes('expand') ||
        node.labelNorm.includes('despleg') ||
        node.labelNorm.includes('mostrar') ||
        node.labelNorm.includes('chevron-right') ||
        node.labelNorm.includes('angle-right') ||
        node.labelNorm.includes('caret-right') ||
        node.labelNorm.includes('arrow right') ||
        node.labelNorm.includes('arrow_right') ||
        ['›', '>', '+'].includes(node.text.trim())
      );
      return nearSameRow && leftOfSubject && looksExpandable;
    }).sort((a, b) => b.rect.left - a.rect.left);
    for (const control of controls.slice(0, 2)) {
      try {
        control.el.click();
        clicked += 1;
        return;
      } catch (error) {
        // Keep trying nearby candidates.
      }
    }
  };

  for (const subject of subjects) {
    const subjectNode = nodes
      .filter((node) => node.text && node.norm.includes(subject))
      .sort((a, b) => a.rect.left - b.rect.left || a.cy - b.cy)[0];
    if (subjectNode) clickNearSubject(subjectNode);
  }
  return { clicked };
}
"""
SCHOOLNET_GRADES_TABLE_SCRIPT = """
() => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const gradeRe = /^(?:[1-7][,.][0-9])$/;
  const cleanGrade = (value) => value.replace('.', ',').trim();
  const headerNames = new Set(['asignatura', 'pond.', 'pond', '1', '2', '3', 'p1', 'p2', 'nf']);
  const knownSubjects = [
    'lenguaje y comunicacion',
    'idioma extranjero ingles',
    'matematica',
    'historia geografia y ciencias sociales',
    'ciencias naturales',
    'artes visuales',
    'musica',
    'tecnologia',
    'educacion fisica y salud',
    'religion',
    'promedios',
  ];

  const linesFromTables = [];
  for (const table of Array.from(document.querySelectorAll('table'))) {
    if (!isVisible(table)) continue;
    const rows = Array.from(table.querySelectorAll('tr'))
      .map((tr) => Array.from(tr.querySelectorAll('th,td')).map(textOf))
      .filter((cells) => cells.some(Boolean));
    const headerIndex = rows.findIndex((cells) => {
      const normalized = cells.map(normalize);
      return normalized.some((cell) => cell.includes('asignatura')) && normalized.includes('p1');
    });
    if (headerIndex < 0) continue;
    const headers = rows[headerIndex].map(normalize);
    const subjectIndex = Math.max(0, headers.findIndex((cell) => cell.includes('asignatura')));
    const p1Index = headers.findIndex((cell) => cell === 'p1');
    if (p1Index < 0) continue;
    linesFromTables.push('LECTURA ESTRUCTURADA SCHOOLNET CALIFICACIONES P1');
    linesFromTables.push('Fuente: tabla HTML visible');
    linesFromTables.push('Formato: Asignatura | P1');
    for (const cells of rows.slice(headerIndex + 1)) {
      const subject = (cells[subjectIndex] || '').trim();
      if (!subject) continue;
      const value = cleanGrade(cells[p1Index] || '');
      linesFromTables.push(`${subject} | ${value}`);
    }
  }
  if (linesFromTables.length) return linesFromTables.join('\\n');

  const rawNodes = Array.from(document.querySelectorAll('body *')).map((el) => {
    const text = textOf(el);
    const rect = el.getBoundingClientRect();
    return {
      el,
      text,
      norm: normalize(text),
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      cx: rect.left + rect.width / 2,
      cy: rect.top + rect.height / 2,
      width: rect.width,
      height: rect.height,
    };
  }).filter((node) => node.text && node.text.length <= 180 && node.width > 0 && node.height > 0);

  const nodes = rawNodes.filter((node) => {
    const children = Array.from(node.el.children || []);
    return !children.some((child) => {
      const childText = textOf(child);
      const childRect = child.getBoundingClientRect();
      return childText && childRect.width > 0 && childRect.height > 0 && normalize(childText) === node.norm;
    });
  });

  const p1Headers = nodes.filter((node) => node.norm === 'p1').sort((a, b) => a.top - b.top || a.left - b.left);
  const subjectHeader = nodes.find((node) => node.norm.includes('asignatura'));
  if (!p1Headers.length || !subjectHeader) return '';
  const p1Header = p1Headers[0];
  const headerY = Math.min(subjectHeader.cy, p1Header.cy);

  const rows = [];
  const addRow = (node) => {
    if (!node || node.cy <= headerY + 5 || node.left > p1Header.left - 60) return;
    if (rows.some((row) => Math.abs(row.cy - node.cy) < 8 || row.norm === node.norm)) return;
    rows.push(node);
  };

  for (const subject of knownSubjects) {
    const candidate = nodes
      .filter((node) => node.norm.includes(subject) && node.left < p1Header.left - 80 && node.cy > headerY + 5)
      .sort((a, b) => a.cy - b.cy || a.left - b.left)[0];
    addRow(candidate);
  }

  for (const node of nodes) {
    if (node.cy <= headerY + 5 || node.left < subjectHeader.left - 40 || node.left > p1Header.left - 80) continue;
    if (headerNames.has(node.norm) || gradeRe.test(node.text) || node.text.length < 3) continue;
    const looksLikeSubject = knownSubjects.some((subject) => node.norm.includes(subject));
    if (looksLikeSubject) addRow(node);
  }

  rows.sort((a, b) => a.cy - b.cy);
  if (!rows.length) return '';
  const output = [
    'LECTURA ESTRUCTURADA SCHOOLNET CALIFICACIONES P1',
    'Fuente: geometria visual de la tabla',
    'Formato: Asignatura | P1',
  ];

  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    const previousY = index === 0 ? headerY : rows[index - 1].cy;
    const nextY = index === rows.length - 1 ? row.cy + (row.cy - previousY || 56) : rows[index + 1].cy;
    const top = (previousY + row.cy) / 2;
    const bottom = (row.cy + nextY) / 2;
    const grade = nodes
      .filter((node) => gradeRe.test(node.text) && node.cy >= top && node.cy < bottom)
      .map((node) => ({ node, distance: Math.abs(node.cx - p1Header.cx) }))
      .filter((item) => item.distance < 45)
      .sort((a, b) => a.distance - b.distance)[0];
    const label = row.norm === 'promedios' ? 'Promedio P1' : row.text;
    output.push(`${label} | ${grade ? cleanGrade(grade.node.text) : ''}`);
  }

  return output.join('\\n');
}
"""
SCHOOLNET_GRADES_DETAIL_SCRIPT = """
() => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const loose = (value) => normalize(value).replace(/[^a-z0-9]+/g, ' ').replace(/\\s+/g, ' ').trim();
  const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
  const gradeRe = /^(?:[1-7][,.][0-9])$/;
  const cleanGrade = (value) => value.replace('.', ',').trim();
  const canonicalColumns = [
    { label: 'Pond.', aliases: ['pond.', 'pond'] },
    { label: '1', aliases: ['1'] },
    { label: '2', aliases: ['2'] },
    { label: '3', aliases: ['3'] },
    { label: 'P1', aliases: ['p1'] },
    { label: 'P2', aliases: ['p2'] },
    { label: 'NF', aliases: ['nf'] },
  ];
  const headerNames = new Set(['asignatura', 'pond.', 'pond', '1', '2', '3', 'p1', 'p2', 'nf']);
  const knownSubjects = [
    'lenguaje y comunicacion',
    'idioma extranjero ingles',
    'matematica',
    'historia geografia y ciencias sociales',
    'ciencias naturales',
    'artes visuales',
    'musica',
    'tecnologia',
    'educacion fisica y salud',
    'religion',
  ];

  const rawNodes = Array.from(document.querySelectorAll('body *')).map((el) => {
    const text = textOf(el);
    const rect = el.getBoundingClientRect();
    return {
      el,
      text,
      norm: normalize(text),
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      cx: rect.left + rect.width / 2,
      cy: rect.top + rect.height / 2,
      width: rect.width,
      height: rect.height,
    };
  }).filter((node) => node.text && node.text.length <= 220 && node.width > 0 && node.height > 0);

  const nodes = rawNodes.filter((node) => {
    const children = Array.from(node.el.children || []);
    return !children.some((child) => {
      const childText = textOf(child);
      const childRect = child.getBoundingClientRect();
      return childText && childRect.width > 0 && childRect.height > 0 && normalize(childText) === node.norm;
    });
  });

  const subjectHeader = nodes.find((node) => node.norm.includes('asignatura'));
  if (!subjectHeader) return '';
  const headerY = subjectHeader.cy;
  const columns = [];
  for (const column of canonicalColumns) {
    const header = nodes
      .filter((node) => column.aliases.includes(node.norm) && node.cy >= headerY - 35 && node.cy <= headerY + 35 && node.cx > subjectHeader.cx)
      .sort((a, b) => Math.abs(a.cy - headerY) - Math.abs(b.cy - headerY) || a.left - b.left)[0];
    if (header) columns.push({ ...column, cx: header.cx });
  }
  if (!columns.length) return '';
  const firstDataX = Math.min(...columns.map((column) => column.cx));

  const gradeNodes = nodes.filter((node) => gradeRe.test(node.text) && node.cx > firstDataX - 50);
  const candidateLabels = nodes
    .filter((node) => {
      if (node.cy <= headerY + 8 || node.cx >= firstDataX - 25) return false;
      if (headerNames.has(node.norm)) return false;
      if (gradeRe.test(node.text) || /[1-7][,.][0-9]/.test(node.text)) return false;
      if (!/[a-zA-ZáéíóúÁÉÍÓÚñÑ]/.test(node.text)) return false;
      return true;
    })
    .sort((a, b) => a.cy - b.cy || a.left - b.left);

  const rows = [];
  for (const node of candidateLabels) {
    const existing = rows.find((row) => Math.abs(row.cy - node.cy) < 9);
    if (existing) {
      if (node.text.length > existing.text.length && node.left <= existing.left + 80) {
        Object.assign(existing, node);
      }
      continue;
    }
    rows.push({ ...node });
  }
  if (!rows.length) return '';

  const output = [
    'DETALLE CRUDO ESTRUCTURADO SCHOOLNET CALIFICACIONES',
    'Fuente: geometria visual de la tabla expandida',
    'Formato: Tipo | Asignatura padre | Fila visible | Pond. | 1 | 2 | 3 | P1 | P2 | NF',
  ];

  let currentSubject = '';
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    const previousY = index === 0 ? headerY : rows[index - 1].cy;
    const nextY = index === rows.length - 1 ? row.cy + (row.cy - previousY || 38) : rows[index + 1].cy;
    const top = (previousY + row.cy) / 2;
    const bottom = (row.cy + nextY) / 2;
    const rowGrades = {};
    for (const column of columns) {
      const grade = gradeNodes
        .filter((node) => node.cy >= top && node.cy < bottom)
        .map((node) => ({ node, distance: Math.abs(node.cx - column.cx) }))
        .filter((item) => item.distance < 38)
        .sort((a, b) => a.distance - b.distance)[0];
      rowGrades[column.label] = grade ? cleanGrade(grade.node.text) : '';
    }
    const rowLoose = loose(row.text);
    const isSubject = knownSubjects.some((subject) => rowLoose.includes(loose(subject)));
    const isAverage = row.norm === 'promedios' || row.norm === 'promedio';
    const type = isAverage ? 'Promedio' : (isSubject ? 'Asignatura' : 'Item');
    if (isSubject) currentSubject = row.text;
    const parent = isSubject || isAverage ? '' : currentSubject;
    output.push([
      type,
      parent,
      row.text,
      rowGrades['Pond.'] || '',
      rowGrades['1'] || '',
      rowGrades['2'] || '',
      rowGrades['3'] || '',
      rowGrades['P1'] || '',
      rowGrades['P2'] || '',
      rowGrades['NF'] || '',
    ].join(' | '));
  }

  return output.join('\\n');
}
"""
SCHOOLNET_GRADES_BY_SUBJECT_SCRIPT = """
async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const subjects = [
    { norm: 'lenguaje y comunicacion', label: 'Lenguaje y Comunicacion' },
    { norm: 'idioma extranjero ingles', label: 'Idioma Extranjero: Ingles' },
    { norm: 'matematica', label: 'Matematica' },
    { norm: 'historia geografia y ciencias sociales', label: 'Historia, Geografia y Ciencias Sociales' },
    { norm: 'ciencias naturales', label: 'Ciencias Naturales' },
    { norm: 'artes visuales', label: 'Artes Visuales' },
    { norm: 'musica', label: 'Musica' },
    { norm: 'tecnologia', label: 'Tecnologia' },
    { norm: 'educacion fisica y salud', label: 'Educacion Fisica y Salud' },
    { norm: 'religion', label: 'Religion' },
  ];
  const gradeRe = /^(?:[1-7][,.][0-9])$/;

  const collectNodes = () => {
    const raw = Array.from(document.querySelectorAll('body *')).filter(isVisible).map((el) => {
      const text = textOf(el);
      const rect = el.getBoundingClientRect();
      const label = [
        text,
        el.getAttribute('aria-label'),
        el.getAttribute('title'),
        el.getAttribute('class'),
        el.getAttribute('aria-expanded'),
      ].filter(Boolean).join(' ');
      return {
        el,
        text,
        norm: normalize(text),
        labelNorm: normalize(label),
        left: rect.left,
        top: rect.top,
        bottom: rect.bottom,
        cx: rect.left + rect.width / 2,
        cy: rect.top + rect.height / 2,
        width: rect.width,
        height: rect.height,
      };
    }).filter((node) => node.text && node.text.length <= 260 && node.width > 0 && node.height > 0);
    return raw.filter((node) => {
      const children = Array.from(node.el.children || []);
      return !children.some((child) => {
        const childText = textOf(child);
        const childRect = child.getBoundingClientRect();
        return childText && childRect.width > 0 && childRect.height > 0 && normalize(childText) === node.norm;
      });
    }).sort((a, b) => a.cy - b.cy || a.left - b.left);
  };

  const findSubjectNode = (subject, nodes) => nodes
    .filter((node) => node.norm.includes(subject.norm))
    .sort((a, b) => a.left - b.left || a.cy - b.cy)[0];

  const clickSubject = async (subjectNode, nodes) => {
    const controls = nodes.filter((node) => {
      if (node.el === subjectNode.el) return false;
      const nearSameRow = Math.abs(node.cy - subjectNode.cy) <= 16;
      const leftOrInsideSubject = node.left <= subjectNode.left + 40;
      const looksExpandable = (
        node.labelNorm.includes('false') ||
        node.labelNorm.includes('expand') ||
        node.labelNorm.includes('despleg') ||
        node.labelNorm.includes('mostrar') ||
        node.labelNorm.includes('chevron') ||
        node.labelNorm.includes('angle') ||
        node.labelNorm.includes('caret') ||
        node.labelNorm.includes('arrow') ||
        ['>', '+'].includes(node.text.trim())
      );
      return nearSameRow && leftOrInsideSubject && looksExpandable;
    }).sort((a, b) => b.left - a.left);
    const targets = [...controls.slice(0, 3), subjectNode];
    for (const target of targets) {
      try {
        target.el.scrollIntoView({ block: 'center', inline: 'nearest' });
        await sleep(80);
        target.el.click();
        await sleep(450);
        return true;
      } catch (error) {
        // Keep trying.
      }
    }
    return false;
  };

  const rowLinesForSubject = (subject, subjectNode, nodes) => {
    const subjectNodes = subjects
      .map((candidate) => findSubjectNode(candidate, nodes))
      .filter(Boolean)
      .sort((a, b) => a.cy - b.cy);
    const nextSubject = subjectNodes.find((node) => node.cy > subjectNode.cy + 10);
    const top = subjectNode.cy - 18;
    const bottom = nextSubject ? nextSubject.cy - 18 : subjectNode.cy + 520;
    const sectionNodes = nodes.filter((node) => node.cy >= top && node.cy < bottom);
    const groupedRows = [];
    for (const node of sectionNodes) {
      const row = groupedRows.find((item) => Math.abs(item.cy - node.cy) <= 9);
      if (row) {
        row.nodes.push(node);
        row.cy = (row.cy + node.cy) / 2;
      } else {
        groupedRows.push({ cy: node.cy, nodes: [node] });
      }
    }
    return groupedRows
      .sort((a, b) => a.cy - b.cy)
      .slice(0, 80)
      .map((row, index) => {
        const seen = new Set();
        const cells = row.nodes
          .sort((a, b) => a.left - b.left)
          .map((node) => node.text)
          .filter((text) => {
            const key = normalize(text);
            if (!key || seen.has(key)) return false;
            seen.add(key);
            return true;
          });
        const hasGrade = cells.some((cell) => gradeRe.test(cell));
        const hasLetters = cells.some((cell) => /[A-Za-zÁÉÍÓÚáéíóúÑñ]/.test(cell));
        if (!hasGrade && !hasLetters) return '';
        return `Fila ${index + 1} | ${cells.join(' | ')}`;
      })
      .filter(Boolean);
  };

  const output = [
    'DETALLE POR ASIGNATURA SCHOOLNET CALIFICACIONES',
    'Fuente: apertura secuencial de cada asignatura',
    'Formato: Asignatura seguida por filas visibles mientras esa asignatura esta abierta',
  ];
  let captured = 0;

  for (const subject of subjects) {
    let nodes = collectNodes();
    let subjectNode = findSubjectNode(subject, nodes);
    if (!subjectNode) continue;
    await clickSubject(subjectNode, nodes);
    nodes = collectNodes();
    subjectNode = findSubjectNode(subject, nodes) || subjectNode;
    const rows = rowLinesForSubject(subject, subjectNode, nodes);
    if (!rows.length) continue;
    output.push('');
    output.push(`ASIGNATURA: ${subject.label}`);
    rows.forEach((row) => output.push(row));
    captured += 1;
  }

  return captured ? output.join('\\n') : '';
}
"""
SCHOOLNET_EXPAND_CONDUCT_ROWS_SCRIPT = """
() => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const relevantTerms = ['anotacion', 'observacion', 'conducta', 'positiva', 'negativa', 'profesor', 'docente', 'asignatura', 'fecha'];
  const dateRe = /\\b\\d{1,2}[/-]\\d{1,2}(?:[/-]\\d{2,4})?\\b/;
  let clicked = 0;
  const nodes = Array.from(document.querySelectorAll('body *')).filter(isVisible).map((el) => {
    const rect = el.getBoundingClientRect();
    const text = textOf(el);
    const label = [
      text,
      el.getAttribute('aria-label'),
      el.getAttribute('title'),
      el.getAttribute('class'),
      el.getAttribute('aria-expanded'),
    ].filter(Boolean).join(' ');
    return { el, text, label, norm: normalize(text), labelNorm: normalize(label), rect, cy: rect.top + rect.height / 2 };
  }).filter((node) => node.labelNorm && node.label.length <= 320);

  const likelyRows = nodes.filter((node) => {
    if (!node.text) return false;
    return dateRe.test(node.text) || relevantTerms.some((term) => node.norm.includes(term));
  });

  for (const rowNode of likelyRows.slice(0, 80)) {
    const controls = nodes.filter((node) => {
      if (node.el === rowNode.el) return false;
      const nearSameRow = Math.abs(node.cy - rowNode.cy) <= 14;
      const leftOfRow = node.rect.left <= rowNode.rect.left + 20;
      const looksExpandable = (
        node.labelNorm.includes('false') ||
        node.labelNorm.includes('expand') ||
        node.labelNorm.includes('despleg') ||
        node.labelNorm.includes('mostrar') ||
        node.labelNorm.includes('chevron-right') ||
        node.labelNorm.includes('angle-right') ||
        node.labelNorm.includes('caret-right') ||
        node.labelNorm.includes('arrow right') ||
        node.labelNorm.includes('arrow_right') ||
        ['>', '+'].includes(node.text.trim())
      );
      return nearSameRow && leftOfRow && looksExpandable;
    }).sort((a, b) => b.rect.left - a.rect.left);
    for (const control of controls.slice(0, 1)) {
      try {
        control.el.click();
        clicked += 1;
      } catch (error) {
        // Keep trying other rows.
      }
      break;
    }
    if (clicked >= 40) break;
  }
  return { clicked };
}
"""
SCHOOLNET_CLICK_CONDUCTA_CATEGORY_SCRIPT = """
(terms) => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const wanted = (Array.isArray(terms) ? terms : [terms]).map(normalize).filter(Boolean);
  const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const interactiveSelector = 'button,[role="tab"],[role="button"],a,[aria-label],[title]';
  const candidates = Array.from(document.querySelectorAll(`${interactiveSelector},span,div`))
    .filter(isVisible)
    .map((el) => {
      const target = el.closest(interactiveSelector) || el;
      const text = textOf(el);
      const label = [
        text,
        el.getAttribute('aria-label'),
        el.getAttribute('title'),
        target.getAttribute('aria-label'),
        target.getAttribute('title'),
      ].filter(Boolean).join(' ');
      const norm = normalize(label);
      const rect = el.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      let score = 0;
      for (const term of wanted) {
        if (norm === term) score += 200;
        else if (norm.includes(term)) score += Math.max(40, term.length * 6);
      }
      if (norm.includes('anotacion')) score += 20;
      if (norm.includes('conducta')) score += 8;
      if (target.matches('button,[role="tab"],[role="button"],a')) score += 12;
      if (String(target.getAttribute('aria-selected') || '').toLowerCase() === 'true') score -= 20;
      score -= Math.max(0, label.length - 90) / 2;
      return {
        el,
        target,
        text: text || label,
        norm,
        score,
        area: targetRect.width * targetRect.height || rect.width * rect.height,
      };
    })
    .filter((item) => item.score > 0 && item.text.length <= 180)
    .sort((a, b) => b.score - a.score || a.area - b.area);

  for (const item of candidates.slice(0, 8)) {
    try {
      item.target.scrollIntoView({ block: 'center', inline: 'center' });
      item.target.click();
      return { clicked: true, text: item.text, score: item.score };
    } catch (error) {
      // Try the next plausible control.
    }
  }
  return { clicked: false };
}
"""
SCHOOLNET_CONDUCTA_DETAIL_SCRIPT = """
() => {
  const normalize = (value) => (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const dateRe = /\\b\\d{1,2}[/-]\\d{1,2}(?:[/-]\\d{2,4})?\\b/;
  const relevantTerms = ['anotacion', 'observacion', 'conducta', 'positiva', 'negativa', 'profesor', 'docente', 'asignatura', 'fecha', 'descripcion', 'detalle'];
  const output = [];
  const seen = new Set();
  const addLine = (line) => {
    const cleaned = (line || '').replace(/\\s+/g, ' ').trim();
    const key = normalize(cleaned);
    if (!cleaned || seen.has(key)) return;
    seen.add(key);
    output.push(cleaned);
  };

  for (const table of Array.from(document.querySelectorAll('table'))) {
    if (!isVisible(table)) continue;
    const rows = Array.from(table.querySelectorAll('tr'))
      .map((tr) => Array.from(tr.querySelectorAll('th,td')).map(textOf).filter(Boolean))
      .filter((cells) => cells.length);
    const tableText = normalize(rows.map((cells) => cells.join(' ')).join(' '));
    const relevant = relevantTerms.some((term) => tableText.includes(term)) || dateRe.test(tableText);
    if (!relevant) continue;
    if (!output.length) {
      addLine('DETALLE CRUDO ESTRUCTURADO SCHOOLNET CONDUCTA');
      addLine('Fuente: tabla HTML visible');
      addLine('Formato: Fila | Celdas visibles en orden');
    }
    rows.forEach((cells, index) => addLine(`Fila tabla ${index + 1} | ${cells.join(' | ')}`));
  }

  const rawNodes = Array.from(document.querySelectorAll('body *')).map((el) => {
    const text = textOf(el);
    const rect = el.getBoundingClientRect();
    return {
      el,
      text,
      norm: normalize(text),
      left: rect.left,
      top: rect.top,
      bottom: rect.bottom,
      cx: rect.left + rect.width / 2,
      cy: rect.top + rect.height / 2,
      width: rect.width,
      height: rect.height,
    };
  }).filter((node) => node.text && node.text.length <= 320 && node.width > 0 && node.height > 0);

  const nodes = rawNodes.filter((node) => {
    const children = Array.from(node.el.children || []);
    return !children.some((child) => {
      const childText = textOf(child);
      const childRect = child.getBoundingClientRect();
      return childText && childRect.width > 0 && childRect.height > 0 && normalize(childText) === node.norm;
    });
  }).sort((a, b) => a.cy - b.cy || a.left - b.left);

  const groupedRows = [];
  for (const node of nodes) {
    const row = groupedRows.find((item) => Math.abs(item.cy - node.cy) <= 9);
    if (row) {
      row.nodes.push(node);
      row.cy = (row.cy + node.cy) / 2;
    } else {
      groupedRows.push({ cy: node.cy, nodes: [node] });
    }
  }

  const relevantRowIndexes = new Set();
  groupedRows.forEach((row, index) => {
    const rowText = row.nodes.map((node) => node.text).join(' ');
    const normalized = normalize(rowText);
    if (dateRe.test(rowText) || relevantTerms.some((term) => normalized.includes(term))) {
      relevantRowIndexes.add(index);
      relevantRowIndexes.add(index - 1);
      relevantRowIndexes.add(index + 1);
      relevantRowIndexes.add(index + 2);
    }
  });

  const rowLines = groupedRows
    .map((row, index) => ({ row, index }))
    .filter((item) => relevantRowIndexes.has(item.index))
    .slice(0, 260)
    .map((item) => {
      const cells = item.row.nodes
        .sort((a, b) => a.left - b.left)
        .map((node) => node.text)
        .filter((text, index, all) => text && all.indexOf(text) === index);
      return `Fila visual ${item.index + 1} | ${cells.join(' | ')}`;
    });

  if (rowLines.length) {
    if (!output.length) {
      addLine('DETALLE CRUDO ESTRUCTURADO SCHOOLNET CONDUCTA');
      addLine('Fuente: geometria visual de filas');
      addLine('Formato: Fila | Celdas visibles en orden');
    } else {
      addLine('LECTURA VISUAL COMPLEMENTARIA SCHOOLNET CONDUCTA');
    }
    rowLines.forEach(addLine);
  }

  return output.join('\\n');
}
"""
SCHOOLNET_CONDUCTA_TERMS = ["Conducta", "Anotaciones", "Observaciones", "Convivencia"]
SCHOOLNET_CALIFICACIONES_TERMS = ["Calificaciones", "Calificacion", "Calificación", "Notas", "Evaluaciones"]
CLASSROOM_COURSE_TERMS = ["4-A", "4 - A", "4 A", "4A", "4°A", "4° A", "4ºA", "4º A", "Cuarto A", "4to A"]
CLASSROOM_CLASSWORK_TERMS = ["Trabajo de clase", "Classwork", "Tareas", "Assignments"]
CLASSROOM_TOPIC_FILTER_TERMS = ["Todos los temas", "All topics", "Filtro por tema", "Filter by topic"]
CLASSROOM_TOPIC_LABEL_TERMS = [
    "matem",
    "matematica",
    "matematicas",
    "ingl",
    "ingles",
    "leng",
    "lenguaje",
    "comunic",
    "comunicacion",
    "ciencias sociales",
    "ciencias naturales",
    "historia",
    "relig",
    "religion",
    "artes",
    "musi",
    "musica",
    "computacion",
    "tecnolog",
    "tecnologia",
    "educacion fisica",
    "fisica",
    "orientacion",
]
CLASSROOM_TOPIC_EXCLUDED_LABELS = {
    "inicio",
    "calendar",
    "calendario",
    "gemini",
    "cursos en los que te has inscrito",
    "clases archivadas",
    "archived classes",
    "ajustes",
    "settings",
    "personas",
    "tablon",
    "stream",
    "trabajo de clase",
    "filtro por tema",
    "filter by topic",
    "todos los temas",
    "all topics",
}
MAX_CLASSROOM_POSTS_TO_OPEN = 6
MAX_CLASSROOM_POST_AGE_DAYS = 31
MAX_CLASSROOM_TOPIC_VIEWS = 12


def _enable_local_site_packages() -> None:
    if LOCAL_SITE_PACKAGES.exists():
        sys.path.insert(0, str(LOCAL_SITE_PACKAGES))


_enable_local_site_packages()


def configure_runtime_environment() -> None:
    APP_TEMP_DIR.mkdir(exist_ok=True)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
    os.environ["TEMP"] = str(APP_TEMP_DIR)
    os.environ["TMP"] = str(APP_TEMP_DIR)


class AppError(RuntimeError):
    pass


def current_report_date() -> dt.date:
    return dt.date.today()


def is_browser_closed_error(exc: Exception) -> bool:
    message = str(exc).lower()
    closed_markers = (
        "target page, context or browser has been closed",
        "browser has been closed",
        "context has been closed",
        "target closed",
    )
    return any(marker in message for marker in closed_markers)


@dataclass
class PageSnapshot:
    platform: str
    title: str
    url: str
    text: str
    status: str
    notes: list[str]
    stats: dict[str, Any]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def stable_hash(value: Any) -> str:
    if isinstance(value, str):
        payload = clean_text(value)
    else:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()


def stable_id(prefix: str, *parts: Any) -> str:
    joined = "|".join(clean_text(str(part or "")) for part in parts)
    return f"{prefix}_{stable_hash(normalize(joined))[:20]}"


class EvidenceStore:
    """Small local evidence backend; later replace this class with an OCI-backed one."""

    ENTITY_NAMES = (
        "schoolnet_conducta",
        "schoolnet_calificaciones",
        "classroom_topics",
        "classroom_posts",
    )

    def __init__(self, path: Path = EVIDENCE_STORE_PATH) -> None:
        self.path = path
        self.data = self._default_data()
        self.load()

    @classmethod
    def _default_data(cls) -> dict[str, Any]:
        return {
            "version": EVIDENCE_STORE_VERSION,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "entities": {name: {} for name in cls.ENTITY_NAMES},
            "stats": {},
        }

    def load(self) -> None:
        if not self.path.exists():
            self.data = self._default_data()
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("cache root is not an object")
            entities = loaded.setdefault("entities", {})
            for name in self.ENTITY_NAMES:
                entities.setdefault(name, {})
            loaded.setdefault("version", EVIDENCE_STORE_VERSION)
            loaded.setdefault("created_at", now_iso())
            loaded.setdefault("updated_at", now_iso())
            loaded.setdefault("stats", {})
            self.data = loaded
        except Exception:
            backup = self.path.with_name(f"{self.path.stem}.corrupt-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
            try:
                self.path.replace(backup)
            except Exception:
                pass
            self.data = self._default_data()
            self.data["stats"]["last_load_warning"] = f"Cache corrupto reiniciado; backup={backup}"

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._prune_unusable_classroom_records()
        self.data["updated_at"] = now_iso()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _prune_unusable_classroom_records(self) -> int:
        posts = self.entity("classroom_posts")
        removed = 0
        for record_id, record in list(posts.items()):
            if not isinstance(record, dict) or not classroom_cache_record_is_usable(record):
                posts.pop(record_id, None)
                removed += 1
        if removed:
            self.data.setdefault("stats", {})["last_pruned_classroom_posts"] = removed
        return removed

    def entity(self, name: str) -> dict[str, dict[str, Any]]:
        entities = self.data.setdefault("entities", {})
        value = entities.setdefault(name, {})
        return value if isinstance(value, dict) else {}

    def get(self, entity: str, record_id: str) -> dict[str, Any] | None:
        record = self.entity(entity).get(record_id)
        return record if isinstance(record, dict) else None

    def upsert(
        self,
        entity: str,
        record_id: str,
        source: str,
        raw_text: str,
        metadata: dict[str, Any] | None = None,
        status: str = "unknown",
    ) -> str:
        raw_text = clean_text(raw_text)
        if not raw_text or not record_id:
            return "ignored"
        records = self.entity(entity)
        if entity == "classroom_posts" and not classroom_cache_record_is_usable(
            {"raw_text": raw_text, "metadata": dict(metadata or {}), "status": status}
        ):
            records.pop(record_id, None)
            return "ignored"
        existing = records.get(record_id)
        content_hash = stable_hash(raw_text)
        timestamp = now_iso()
        if not isinstance(existing, dict):
            records[record_id] = {
                "id": record_id,
                "source": source,
                "first_seen_at": timestamp,
                "last_seen_at": timestamp,
                "content_hash": content_hash,
                "raw_text": raw_text,
                "metadata": dict(metadata or {}),
                "status": status,
            }
            return "new"
        changed = existing.get("content_hash") != content_hash
        existing["last_seen_at"] = timestamp
        existing["source"] = source or existing.get("source", "")
        existing["status"] = status or existing.get("status", "unknown")
        merged_metadata = dict(existing.get("metadata") or {})
        merged_metadata.update(metadata or {})
        existing["metadata"] = merged_metadata
        if changed:
            existing["content_hash"] = content_hash
            existing["raw_text"] = raw_text
            return "updated"
        return "unchanged"

    def update_from_snapshots(self, snapshots: list[PageSnapshot]) -> dict[str, int]:
        stats = {"new": 0, "updated": 0, "unchanged": 0, "ignored": 0}
        for snapshot in snapshots:
            if snapshot.status not in ("ok", "empty"):
                continue
            if snapshot.platform == "SchoolNet - Conducta":
                for record in parse_conducta_records(snapshot.text, snapshot.platform):
                    result = self.upsert(
                        "schoolnet_conducta",
                        record["id"],
                        snapshot.platform,
                        record["raw_text"],
                        record["metadata"],
                        status="active",
                    )
                    stats[result] = stats.get(result, 0) + 1
            elif snapshot.platform == "SchoolNet - Calificaciones" and snapshot.text:
                grade_text = schoolnet_prefer_canonical_p1(snapshot.text)
                result = self.upsert(
                    "schoolnet_calificaciones",
                    "latest",
                    snapshot.platform,
                    grade_text,
                    {"url": snapshot.url, "title": snapshot.title},
                    status="active",
                )
                stats[result] = stats.get(result, 0) + 1
                history_id = f"snapshot_{stable_hash(grade_text)[:20]}"
                self.upsert(
                    "schoolnet_calificaciones",
                    history_id,
                    snapshot.platform,
                    grade_text,
                    {"url": snapshot.url, "title": snapshot.title, "history": True},
                    status="active",
                )
            elif snapshot.platform.startswith("Google Classroom"):
                for topic in snapshot.stats.get("classroom_topic_urls", []) or []:
                    if isinstance(topic, dict):
                        topic_url = str(topic.get("url") or "")
                        label = clean_text(str(topic.get("label") or "Tema Classroom"))
                        if topic_url:
                            result = self.upsert(
                                "classroom_topics",
                                stable_id("topic", topic_url),
                                snapshot.platform,
                                label,
                                {"url": topic_url, "label": label},
                                status="active",
                            )
                            stats[result] = stats.get(result, 0) + 1
                for record in parse_classroom_post_records(snapshot.text, snapshot.platform):
                    result = self.upsert(
                        "classroom_posts",
                        record["id"],
                        record["source"],
                        record["raw_text"],
                        record["metadata"],
                        status=record.get("status", "unknown"),
                    )
                    stats[result] = stats.get(result, 0) + 1
        self.data.setdefault("stats", {})["last_update"] = stats
        return stats

    def to_page_snapshots(self) -> list[PageSnapshot]:
        snapshots: list[PageSnapshot] = []

        conducta_records = sorted(
            self.entity("schoolnet_conducta").values(),
            key=lambda record: str((record.get("metadata") or {}).get("sort_date") or ""),
            reverse=True,
        )
        if conducta_records:
            canonical_lines = [format_conducta_record_for_prompt(record) for record in conducta_records]
            raw_lines = [clean_text(str(record.get("raw_text") or "")) for record in conducta_records]
            text = "\n".join(
                [
                    "DETALLE CANONICO SCHOOLNET CONDUCTA",
                    "Fuente: cache historico local",
                    "Regla: para describir una anotacion usa siempre Observaciones como Mensaje para mostrar. Motivo tecnico/reglamento se conserva solo como clasificacion o respaldo.",
                    *canonical_lines,
                    "",
                    "DETALLE CRUDO ESTRUCTURADO SCHOOLNET CONDUCTA",
                    "Fuente: cache historico local",
                    "Formato: Fila | Celdas visibles en orden",
                    *raw_lines,
                ]
            )
            snapshots.append(
                PageSnapshot(
                    platform="SchoolNet - Conducta",
                    title="Conducta (cache historico)",
                    url=SCHOOLNET_URL,
                    text=text,
                    status="ok",
                    notes=["Evidencia reconstruida desde cache historico local."],
                    stats={
                        "source_count": len(conducta_records),
                        "frames_seen": 0,
                        "chars": len(text),
                        "lines": len(text.splitlines()),
                        "cache_records": len(conducta_records),
                    },
                )
            )

        grades = self.get("schoolnet_calificaciones", "latest")
        if grades:
            text = schoolnet_prefer_canonical_p1(str(grades.get("raw_text") or ""))
            snapshots.append(
                PageSnapshot(
                    platform="SchoolNet - Calificaciones",
                    title="Calificaciones (ultimo snapshot cacheado)",
                    url=SCHOOLNET_URL,
                    text=text,
                    status="ok",
                    notes=["Ultimo snapshot valido de calificaciones desde cache historico local."],
                    stats={
                        "source_count": 1,
                        "frames_seen": 0,
                        "chars": len(text),
                        "lines": len(text.splitlines()),
                        "cache_records": 1,
                    },
                )
            )

        classroom_records = sorted(
            [
                record
                for record in self.entity("classroom_posts").values()
                if classroom_cache_record_is_usable(record)
            ],
            key=lambda record: (
                str((record.get("metadata") or {}).get("topic") or ""),
                str((record.get("metadata") or {}).get("published_sort") or ""),
                str((record.get("metadata") or {}).get("title") or ""),
            ),
            reverse=True,
        )
        if classroom_records:
            blocks = [
                "DETALLE DE POSTS RELEVANTES ABIERTOS EN GOOGLE CLASSROOM - 4-A - CACHE HISTORICO",
                "Criterio: cache historico local con posts relevantes de Trabajo de clase por tema/asignatura.",
                f"Fecha de corte: {current_report_date().isoformat()}",
            ]
            for index, record in enumerate(classroom_records, start=1):
                metadata = record.get("metadata") or {}
                blocks.extend(
                    [
                        "",
                        f"POST CACHEADO {index}",
                        f"Fuente original: {record.get('source') or metadata.get('source') or 'Google Classroom - 4-A'}",
                        f"Estado cache: {record.get('status', 'unknown')}",
                        f"Asignatura/tema detectado: {metadata.get('topic') or metadata.get('subject') or 'No detectado'}",
                        f"Titulo: {metadata.get('title') or 'No detectado'}",
                        f"URL detalle detectada: {metadata.get('detail_href') or 'No detectada'}",
                        f"Publicado/fecha visible en tarjeta: {metadata.get('posted') or 'No detectada'}",
                        "Texto historico cacheado:",
                        clean_text(str(record.get("raw_text") or ""))[:4200],
                    ]
                )
            text = "\n".join(blocks).strip()
            snapshots.append(
                PageSnapshot(
                    platform="Google Classroom - 4-A - Trabajo de clase - Cache historico",
                    title="Classroom cache historico",
                    url=CLASSROOM_4A_CLASSWORK_URL,
                    text=text,
                    status="ok",
                    notes=["Evidencia reconstruida desde cache historico local."],
                    stats={
                        "source_count": len(classroom_records),
                        "frames_seen": 0,
                        "chars": len(text),
                        "lines": len(text.splitlines()),
                        "cache_records": len(classroom_records),
                    },
                )
            )
        return snapshots

    def summary_snapshot(self) -> PageSnapshot:
        entities = self.data.get("entities", {})
        counts = {
            name: len(value) if isinstance(value, dict) else 0
            for name, value in entities.items()
        }
        text = "\n".join(
            [
                "CACHE HISTORICO LOCAL",
                f"Archivo: {self.path}",
                f"Actualizado: {self.data.get('updated_at', 'No detectado')}",
                *[f"{name}: {count}" for name, count in counts.items()],
            ]
        )
        return PageSnapshot(
            platform="Cache historico local",
            title="EvidenceStore",
            url=str(self.path),
            text=text,
            status="ok",
            notes=[],
            stats={
                "source_count": 1,
                "frames_seen": 0,
                "chars": len(text),
                "lines": len(text.splitlines()),
                "cache_counts": counts,
                "cache_last_update": self.data.get("stats", {}).get("last_update", {}),
            },
        )


def parse_conducta_records(text: str, source: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in clean_text(text).splitlines():
        if not re.match(r"^Fila (?:tabla|visual) ", line):
            continue
        parts = [part.strip() for part in line.split(" | ")]
        if len(parts) < 7 or not re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", parts[1]):
            continue
        metadata = {
            "source": source,
            "fecha": parts[1],
            "motivo": parts[2] if len(parts) > 2 else "",
            "profesor": parts[3] if len(parts) > 3 else "",
            "asignatura": parts[4] if len(parts) > 4 else "",
            "observacion": parts[5] if len(parts) > 5 else "",
            "categoria": parts[6] if len(parts) > 6 else "",
            "sort_date": sort_key_from_chilean_date(parts[1]),
        }
        record_id = stable_id(
            "conducta",
            metadata["fecha"],
            metadata["motivo"],
            metadata["profesor"],
            metadata["asignatura"],
            metadata["observacion"],
            metadata["categoria"],
        )
        if record_id in seen:
            continue
        seen.add(record_id)
        records.append({"id": record_id, "raw_text": line, "metadata": metadata})
    return records


def format_conducta_record_for_prompt(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") or {}
    fecha = clean_text(str(metadata.get("fecha") or "No detectado"))
    motivo = clean_text(str(metadata.get("motivo") or "No detectado"))
    profesor = clean_text(str(metadata.get("profesor") or "No detectado"))
    asignatura = clean_text(str(metadata.get("asignatura") or "No detectado"))
    observacion = clean_text(str(metadata.get("observacion") or ""))
    categoria = clean_text(str(metadata.get("categoria") or "No detectado"))
    mensaje = observacion or "No detectado"
    return (
        f"Anotacion | Fecha: {fecha} | Asignatura: {asignatura} | Profesor: {profesor} | "
        f"Categoria: {categoria} | Mensaje para mostrar: {mensaje} | "
        f"Observaciones: {observacion or 'No detectado'} | Motivo tecnico/reglamento: {motivo}"
    )


def sort_key_from_chilean_date(value: str) -> str:
    match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", value or "")
    if not match:
        return ""
    day, month, year = (int(part) for part in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_classroom_post_records(text: str, fallback_source: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    cleaned = clean_text(text)
    if not cleaned:
        return records
    starts = [match.start() for match in re.finditer(r"(?m)^POST (?:ABIERTO|CAPTURADO|VISIBLE|CACHEADO|REUTILIZADO)", cleaned)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(cleaned)
        block = cleaned[start:end].strip()
        if not block:
            continue
        metadata = parse_classroom_post_metadata(block, fallback_source)
        title = metadata.get("title", "")
        if not title or is_generic_classroom_title(title):
            continue
        detail_href = metadata.get("detail_href", "")
        data_id = metadata.get("data_stream_item_id", "")
        record_id = classroom_cache_record_id(
            {
                "title": title,
                "posted": metadata.get("posted", ""),
                "detail_href": detail_href,
                "data_stream_item_id": data_id,
                "subject": metadata.get("topic", ""),
                "preview": metadata.get("body_text", block),
            },
            fallback_source,
        )
        records.append(
            {
                "id": record_id,
                "source": metadata.get("source") or fallback_source,
                "raw_text": metadata.get("body_text") or block,
                "metadata": metadata,
                "status": classroom_record_status(metadata.get("body_text") or block),
            }
        )
    return records


def parse_classroom_post_metadata(block: str, fallback_source: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"source": fallback_source}
    text_lines = clean_text(block).splitlines()
    body_started = False
    body_lines: list[str] = []
    for line in text_lines:
        if line.startswith("Fuente:"):
            metadata["source"] = line.split(":", 1)[1].strip()
        elif line.startswith("Asignatura/tema detectado:"):
            metadata["topic"] = line.split(":", 1)[1].strip()
            metadata["subject"] = metadata["topic"]
        elif line.startswith("Titulo:"):
            metadata["title"] = line.split(":", 1)[1].strip()
        elif line.startswith("URL detalle detectada:"):
            value = line.split(":", 1)[1].strip()
            metadata["detail_href"] = "" if value == "No detectada" else value
        elif line.startswith("Publicado/fecha visible"):
            value = line.split(":", 1)[1].strip()
            metadata["posted"] = "" if value == "No detectada" else value
            metadata["published_sort"] = first_sortable_date(value)
        elif line.startswith("Fecha de entrega visible:"):
            value = line.split(":", 1)[1].strip()
            metadata["due"] = "" if value == "No detectada" else value
        elif (
            line.startswith("TEXTO CRUDO VISIBLE")
            or line.startswith("LECTURA CRUDA")
            or line.startswith("DETALLE DE POSTS")
            or line.startswith("POSTS RELEVANTES")
            or line.startswith("----- ")
        ):
            body_started = False
            continue
        elif line.startswith("Texto ") or line.startswith("Texto historico cacheado:"):
            body_started = True
            continue
        elif line.startswith("Links visibles"):
            body_started = False
            continue
        elif body_started:
            body_lines.append(line)
    metadata["body_text"] = "\n".join(body_lines).strip()
    return metadata


def first_sortable_date(value: str) -> str:
    mention = latest_date(extract_date_mentions(value, current_report_date()))
    return mention.isoformat() if mention else ""


def classroom_record_status(text: str) -> str:
    dates = extract_date_mentions(text, current_report_date())
    if should_skip_past_classroom_item(dates, current_report_date()):
        return "past"
    if dates:
        return "active"
    return "unknown"


CLASSROOM_ASSESSMENT_CACHE_TITLE_KEYWORDS = [
    "prueba",
    "evaluacion",
    "control",
    "quiz",
    "examen",
    "tarea",
    "entrega",
    "test",
    "exam",
    "homework",
]

CLASSROOM_DETAIL_SIGNAL_TERMS = [
    "temario",
    "resolver",
    "calculo",
    "calculo mental",
    "estimar",
    "identificar",
    "ecuaciones",
    "inecuaciones",
    "leccion",
    "practicar",
    "objetivo",
    "contenido",
    "activity book",
    "vocabulary",
    "wordwall",
    "worksheet",
    "pages",
    "paginas",
    "study",
    "practice",
]


def classroom_text_is_global_noise(text: str) -> bool:
    cleaned = clean_text(str(text or ""))
    if not cleaned:
        return True
    normalized_text = normalize(cleaned)
    if "post candidato no abierto" in normalized_text or "detalle incompleto" in normalized_text:
        return True
    if "texto crudo visible de la vista" in normalized_text:
        return True
    markers = (
        "menu principal",
        "inicio calendar gemini",
        "cursos en los que te has inscrito",
        "tareas pendientes",
        "clases archivadas ajustes",
        "aplicaciones de google",
        "cuenta de google",
        "tema matematicas opciones del tema",
    )
    marker_count = sum(1 for marker in markers if marker in normalized_text)
    title_count = sum(
        1
        for line in cleaned.splitlines()
        if len(line.strip()) <= 220 and has_any_normalized_keyword(line, CLASSROOM_POST_TITLE_KEYWORDS)
    )
    return bool(len(cleaned) > 2400 and (marker_count >= 2 or title_count >= 5))


def classroom_title_needs_complete_detail(title: str) -> bool:
    return has_any_normalized_keyword(title, CLASSROOM_ASSESSMENT_CACHE_TITLE_KEYWORDS)


def classroom_text_has_detail_signal(text: str) -> bool:
    normalized_text = normalize(clean_text(str(text or "")))
    return any(term in normalized_text for term in CLASSROOM_DETAIL_SIGNAL_TERMS)


def classroom_cache_record_is_usable(record: dict[str, Any]) -> bool:
    raw_text = clean_text(str(record.get("raw_text") or ""))
    metadata = record.get("metadata") or {}
    title = clean_text(str(metadata.get("title") or ""))
    if not raw_text or len(raw_text) < 60:
        return False
    if classroom_text_is_global_noise(raw_text):
        return False
    if title and is_generic_classroom_title(title):
        return False
    if title and classroom_title_needs_complete_detail(title):
        if len(raw_text) < 180:
            return False
        if not classroom_text_has_detail_signal(raw_text):
            return False
    return True


def classroom_cache_record_id(candidate: dict[str, Any], platform: str) -> str:
    detail_href = str(candidate.get("detail_href") or candidate.get("href") or "").strip()
    data_id = str(candidate.get("data_stream_item_id") or "").strip()
    if detail_href:
        return stable_id("classroom", detail_href)
    if data_id:
        return stable_id("classroom", CLASSROOM_4A_COURSE_ID, data_id)
    return stable_id(
        "classroom",
        platform,
        candidate.get("subject") or "",
        candidate.get("title") or "",
        candidate.get("posted") or "",
        candidate.get("preview") or candidate.get("body_text") or "",
    )


SCHOOLNET_GRADE_SUBJECTS = [
    "Lenguaje y Comunicación",
    "Idioma Extranjero: Inglés",
    "Matemática",
    "Historia, Geografía Y Ciencias Sociales",
    "Ciencias Naturales",
    "Artes Visuales",
    "Música",
    "Tecnología",
    "Educación Física Y Salud",
    "Religión",
]


def schoolnet_grade_subject_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalize(value)).strip()


def schoolnet_is_grade_subject(value: str) -> bool:
    value_key = schoolnet_grade_subject_key(value)
    return any(schoolnet_grade_subject_key(subject) in value_key for subject in SCHOOLNET_GRADE_SUBJECTS)


def schoolnet_canonical_p1_from_detail(detail_text: str) -> str:
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in clean_text(detail_text).splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 8:
            continue
        row_type = normalize(parts[0])
        subject = clean_text(parts[2])
        p1 = clean_text(parts[7])
        if not subject:
            continue
        if row_type == "promedio":
            label = "Promedio P1"
        elif row_type == "asignatura" or (row_type == "item" and schoolnet_is_grade_subject(subject)):
            label = subject
        else:
            continue
        key = normalize(label)
        if key in seen:
            continue
        seen.add(key)
        rows.append((label, p1))
    if not rows:
        return ""
    lines = [
        "CALIFICACIONES P1 CANONICAS SCHOOLNET",
        "Fuente: columna P1 del bloque visual estructurado de SchoolNet.",
        "Regla: usar estos valores para la infografia; las columnas 1, 2 y 3 son notas parciales, no promedios.",
        "Formato: Asignatura | P1",
    ]
    lines.extend(f"{subject} | {p1}" for subject, p1 in rows)
    return "\n".join(lines)


def schoolnet_prefer_canonical_p1(text: str) -> str:
    text = clean_text(text)
    canonical = schoolnet_canonical_p1_from_detail(text)
    if not canonical:
        return text
    cleaned_lines: list[str] = []
    skipping = False
    for line in text.splitlines():
        normalized_line = normalize(line)
        starts_conflicting_p1 = normalized_line == "lectura estructurada schoolnet calificaciones p1"
        starts_existing_canonical = normalized_line == "calificaciones p1 canonicas schoolnet"
        if starts_conflicting_p1 or starts_existing_canonical:
            skipping = True
            continue
        if skipping and (
            normalized_line.startswith("detalle crudo estructurado schoolnet calificaciones")
            or normalized_line.startswith("detalle por asignatura schoolnet calificaciones")
            or normalized_line.startswith("texto crudo visible")
            or normalized_line.startswith("lectura cruda")
        ):
            skipping = False
        if skipping:
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines).strip()
    return f"{canonical}\n\n{cleaned}".strip()


class BrowserController:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._worker_thread: threading.Thread | None = None
        self._worker_queue: queue.Queue[Any] = queue.Queue()
        self._progress_lock = threading.RLock()
        self._progress_events: list[dict[str, Any]] = []
        self._progress_started_at: float | None = None
        self._progress_running = False
        self._playwright: Any | None = None
        self._context: Any | None = None
        self._browser_started_at: float | None = None
        self._evidence_store: EvidenceStore | None = None
        self._force_full_scan = False

    def _ensure_worker_thread(self) -> None:
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._worker_queue = queue.Queue()
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="ResumenEscolarPlaywright",
                daemon=True,
            )
            self._worker_thread.start()

    def _worker_loop(self) -> None:
        while True:
            task = self._worker_queue.get()
            if task is None:
                return
            try:
                task["result"] = task["func"]()
            except BaseException as exc:
                task["exception"] = exc
            finally:
                task["done"].set()

    def _call_browser_thread(self, func: Any) -> Any:
        if threading.current_thread() is self._worker_thread:
            return func()
        self._ensure_worker_thread()
        done = threading.Event()
        task: dict[str, Any] = {"func": func, "done": done, "result": None, "exception": None}
        self._worker_queue.put(task)
        done.wait()
        if task.get("exception") is not None:
            raise task["exception"]
        return task.get("result")

    def _reset_progress(self, label: str) -> None:
        with self._progress_lock:
            self._progress_started_at = time.perf_counter()
            self._progress_running = True
            self._progress_events = []
        self._progress(label, status="start")

    def _finish_progress(self, label: str, status: str = "done") -> None:
        self._progress(label, status=status)
        with self._progress_lock:
            self._progress_running = False

    def _progress(self, step: str, detail: str = "", status: str = "info", duration_s: float | None = None) -> float:
        now = time.perf_counter()
        with self._progress_lock:
            started_at = self._progress_started_at or now
            event = {
                "step": step,
                "detail": detail,
                "status": status,
                "elapsed_s": round(now - started_at, 2),
                "duration_s": round(duration_s, 2) if duration_s is not None else None,
                "clock": dt.datetime.now().strftime("%H:%M:%S"),
            }
            self._progress_events.append(event)
            self._progress_events = self._progress_events[-120:]
        return now

    def _progress_done(self, step: str, started_at: float, detail: str = "") -> None:
        self._progress(step, detail=detail, status="done", duration_s=time.perf_counter() - started_at)

    def progress(self) -> dict[str, Any]:
        with self._progress_lock:
            return {
                "running": self._progress_running,
                "events": list(self._progress_events),
            }

    def _browser_path(self) -> Path:
        if CHROME_EXE.exists():
            return CHROME_EXE
        if EDGE_EXE.exists():
            return EDGE_EXE
        raise AppError(
            "No encontre Chrome ni Edge instalados en las rutas esperadas. "
            "Instala Chrome o ajusta CHROME_EXE en resumen_escolar/app.py."
        )

    def _discard_browser_state(self) -> None:
        context = self._context
        playwright = self._playwright
        self._context = None
        self._playwright = None
        self._browser_started_at = None

        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    def ensure_started(self) -> None:
        with self._lock:
            if self._context is not None:
                try:
                    pages = list(self._context.pages)
                    for page in pages[:1]:
                        _ = getattr(page, "url", "")
                    return
                except Exception:
                    self._discard_browser_state()

            try:
                from playwright.sync_api import sync_playwright
            except ModuleNotFoundError as exc:
                raise AppError(
                    "Falta instalar Playwright para Python. Ejecuta: "
                    "python -m pip install --target .runtime\\site-packages playwright"
                ) from exc

            RUNTIME_DIR.mkdir(exist_ok=True)
            configure_runtime_environment()
            profile_dir = RUNTIME_DIR / "chrome-profile"
            profile_dir.mkdir(exist_ok=True)

            browser_path = self._browser_path()
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                executable_path=str(browser_path),
                headless=False,
                viewport=None,
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            self._browser_started_at = time.time()

    def open_platforms(self) -> dict[str, Any]:
        return self._call_browser_thread(self._open_platforms_with_retry)

    def _open_platforms_with_retry(self) -> dict[str, Any]:
        for attempt in range(2):
            try:
                return self._open_platforms()
            except Exception as exc:
                if is_browser_closed_error(exc):
                    self._discard_browser_state()
                    if attempt == 0:
                        continue
                    raise AppError(
                        "Chrome controlado se cerro antes de completar la apertura. "
                        "Vuelve a presionar Abrir plataformas."
                    ) from exc
                raise
        raise AppError("No pude abrir las plataformas.")

    def _open_platforms(self) -> dict[str, Any]:
        self.ensure_started()
        assert self._context is not None

        pages = self._context.pages
        schoolnet_page = self._find_page(pages, "schoolnet")
        classroom_page = self._find_page(pages, "classroom.google")

        if schoolnet_page is None:
            schoolnet_page = self._context.new_page()
            schoolnet_page.goto(SCHOOLNET_URL, wait_until="domcontentloaded")
        else:
            schoolnet_page.bring_to_front()

        if classroom_page is None:
            classroom_page = self._context.new_page()
            classroom_page.goto(CLASSROOM_4A_CLASSWORK_URL, wait_until="domcontentloaded")
        else:
            classroom_page.bring_to_front()
            if not self._is_classroom_4a_classwork_page(classroom_page):
                try:
                    classroom_page.goto(CLASSROOM_4A_CLASSWORK_URL, wait_until="domcontentloaded")
                except Exception:
                    pass

        return {
            "ok": True,
            "message": (
                "Abri Chrome controlado. Inicia sesion si hace falta y deja "
                "SchoolNet/Classroom en las vistas que quieres resumir."
            ),
            "pages": [
                self._page_summary(schoolnet_page, "SchoolNet"),
                self._page_summary(classroom_page, "Google Classroom"),
            ],
        }

    def snapshots(self) -> list[PageSnapshot]:
        return self._call_browser_thread(self._snapshots_with_progress)

    def _snapshots_with_progress(self) -> list[PageSnapshot]:
        self._reset_progress("Diagnostico: inicio")
        try:
            for attempt in range(2):
                try:
                    snapshots = self._snapshots_once()
                    self._finish_progress("Diagnostico: terminado")
                    return snapshots
                except Exception as exc:
                    if is_browser_closed_error(exc):
                        self._discard_browser_state()
                        if attempt == 0:
                            continue
                        self._finish_progress("Diagnostico: error", status="error")
                        raise AppError(
                            "Chrome controlado estaba cerrado. Presiona Abrir plataformas, "
                            "inicia sesion si hace falta y vuelve a generar."
                        ) from exc
                    self._finish_progress("Diagnostico: error", status="error")
                    raise
            self._finish_progress("Diagnostico: terminado")
            return []
        except Exception:
            with self._progress_lock:
                self._progress_running = False
            raise

    def targeted_snapshots(
        self,
        force_full_scan: bool = False,
        evidence_store: EvidenceStore | None = None,
    ) -> list[PageSnapshot]:
        return self._call_browser_thread(
            lambda: self._targeted_snapshots_with_progress(force_full_scan, evidence_store)
        )

    def _targeted_snapshots_with_progress(
        self,
        force_full_scan: bool = False,
        evidence_store: EvidenceStore | None = None,
    ) -> list[PageSnapshot]:
        self._evidence_store = evidence_store
        self._force_full_scan = bool(force_full_scan)
        self._reset_progress("Generacion: inicio")
        try:
            for attempt in range(2):
                try:
                    snapshots = self._targeted_snapshots_once()
                    self._finish_progress("Generacion: terminado")
                    return snapshots
                except Exception as exc:
                    if is_browser_closed_error(exc):
                        self._discard_browser_state()
                        if attempt == 0:
                            continue
                        self._finish_progress("Generacion: error", status="error")
                        raise AppError(
                            "Chrome controlado estaba cerrado. Presiona Abrir plataformas, "
                            "inicia sesion si hace falta y vuelve a generar."
                        ) from exc
                    self._finish_progress("Generacion: error", status="error")
                    raise
            self._finish_progress("Generacion: terminado")
            return []
        except Exception:
            with self._progress_lock:
                self._progress_running = False
            raise
        finally:
            self._evidence_store = None
            self._force_full_scan = False

    def _snapshots_once(self) -> list[PageSnapshot]:
        self.ensure_started()
        assert self._context is not None
        snapshots: list[PageSnapshot] = []
        for page in list(self._context.pages):
            platform = classify_platform(getattr(page, "url", "") or "")
            if platform is None:
                continue
            snapshots.append(self._snapshot_page(page, platform))
        return snapshots

    def _targeted_snapshots_once(self) -> list[PageSnapshot]:
        self.ensure_started()
        assert self._context is not None

        snapshots: list[PageSnapshot] = []
        pages = self._context.pages
        schoolnet_page = self._find_page(pages, "schoolnet")
        classroom_page = self._find_page(pages, "classroom.google")

        if schoolnet_page is None:
            schoolnet_page = self._context.new_page()
            schoolnet_page.goto(SCHOOLNET_URL, wait_until="domcontentloaded")
            snapshots.append(
                self._message_snapshot(
                    "SchoolNet",
                    SCHOOLNET_URL,
                    "needs_login",
                    ["Abri SchoolNet. Inicia sesion y vuelve a generar para leer Conducta y Calificaciones."],
                )
            )
        else:
            snapshots.extend(self._collect_schoolnet_snapshots(schoolnet_page))

        if classroom_page is None:
            classroom_page = self._context.new_page()
            classroom_page.goto(CLASSROOM_4A_CLASSWORK_URL, wait_until="domcontentloaded")
            snapshots.append(
                self._message_snapshot(
                    "Google Classroom - 4-A",
                    CLASSROOM_4A_CLASSWORK_URL,
                    "needs_login",
                    ["Abri Classroom 4-A Trabajo de clase. Inicia sesion y vuelve a generar si hace falta."],
                )
            )
        else:
            snapshots.extend(self._collect_classroom_snapshots(classroom_page))

        return snapshots

    def _collect_schoolnet_snapshots(self, page: Any) -> list[PageSnapshot]:
        snapshots: list[PageSnapshot] = []
        page.bring_to_front()

        snapshots.append(
            self._navigate_and_snapshot(
                page,
                "SchoolNet - Conducta",
                SCHOOLNET_CONDUCTA_TERMS,
                "No pude encontrar/clickear la seccion Conducta automaticamente. Dejala abierta manualmente y vuelve a generar.",
            )
        )
        snapshots.append(
            self._navigate_and_snapshot(
                page,
                "SchoolNet - Calificaciones",
                SCHOOLNET_CALIFICACIONES_TERMS,
                "No pude encontrar/clickear la seccion Calificaciones automaticamente. Dejala abierta manualmente y vuelve a generar.",
            )
        )
        return snapshots

    def _collect_classroom_snapshots(self, page: Any) -> list[PageSnapshot]:
        notes: list[str] = []
        snapshots: list[PageSnapshot] = []
        page.bring_to_front()

        step_started = self._progress("Classroom: abrir URL fija 4-A Trabajo de clase")
        try:
            page.goto(CLASSROOM_4A_CLASSWORK_URL, wait_until="domcontentloaded", timeout=10000)
            self._wait_after_classroom_interaction(page)
        except Exception as exc:
            notes.append(f"No pude abrir la URL fija de Trabajo de clase 4-A: {exc}")

        self._progress_done(
            "Classroom: abrir URL fija 4-A Trabajo de clase",
            step_started,
            f"url={getattr(page, 'url', '') or 'sin url'}",
        )

        if not self._is_classroom_4a_listing_page(page):
            url = getattr(page, "url", "") or ""
            notes.append(f"Bloqueo: Classroom quedo en una ruta global o incorrecta, no en Trabajo de clase 4-A: {url}")
            snapshots.append(
                self._message_snapshot(
                    "Google Classroom - 4-A - Trabajo de clase",
                    url,
                    "error",
                    notes,
                )
            )
            return snapshots

        classwork_url = getattr(page, "url", "") or CLASSROOM_4A_CLASSWORK_URL
        direct_topic_links = self._read_classroom_topic_links(page)
        topic_labels = [topic["label"] for topic in direct_topic_links] if direct_topic_links else self._classroom_topic_labels_from_filter(page)
        classwork_snapshot = self._message_snapshot(
            "Google Classroom - 4-A - Trabajo de clase",
            classwork_url or CLASSROOM_URL,
            "ok" if topic_labels else "empty",
            notes + [
                (
                    "Temas/asignaturas detectados desde Filtro por tema: "
                    + (" | ".join(topic_labels) if topic_labels else "No detectado")
                ),
                (
                    "URLs reales de tema detectadas: "
                    + (
                        " | ".join(f"{topic.get('label', 'Tema')}: {topic.get('url', '')}" for topic in direct_topic_links)
                        if direct_topic_links
                        else "No detectadas"
                    )
                ),
            ],
        )
        classwork_snapshot.stats["classroom_topic_count"] = len(topic_labels)
        classwork_snapshot.stats["classroom_topic_urls"] = direct_topic_links
        snapshots.append(classwork_snapshot)

        if direct_topic_links:
            snapshots.extend(self._collect_classroom_topic_snapshots_by_url(page, direct_topic_links, classwork_url))
            return snapshots

        if topic_labels:
            snapshots.extend(self._collect_classroom_topic_snapshots(page, topic_labels, classwork_url))
            return snapshots

        fallback = self._snapshot_classroom_page(
            page,
            "Google Classroom - 4-A - Trabajo en clase - Sin filtro por tema",
            ["No detecte temas/asignaturas en Filtro por tema; lei la vista actual como respaldo."],
        )
        snapshots.append(fallback)
        return snapshots

    def _collect_classroom_topic_snapshots(
        self,
        page: Any,
        topic_labels: list[str],
        classwork_url: str,
    ) -> list[PageSnapshot]:
        snapshots: list[PageSnapshot] = []
        for index, label in enumerate(topic_labels[:MAX_CLASSROOM_TOPIC_VIEWS], start=1):
            step_label = f"Classroom: abrir tema {index}/{len(topic_labels)} - {label}"
            step_started = self._progress(step_label)
            opened = self._open_classroom_topic_from_filter(page, label, classwork_url)
            current_url = getattr(page, "url", "") or ""
            self._progress_done(
                step_label,
                step_started,
                f"abierto={'si' if opened else 'no'} url={current_url or 'sin url'}",
            )
            if not opened or not self._is_classroom_4a_topic_page(page):
                snapshots.append(
                    self._message_snapshot(
                        f"Google Classroom - 4-A - Trabajo de clase - Tema: {label[:70]}",
                        current_url,
                        "error",
                        [
                            f"No pude abrir este tema desde Filtro por tema: {label}.",
                            "No se leyo la vista actual porque no corresponde a una URL real de tema 4-A.",
                        ],
                    )
                )
                try:
                    page.goto(CLASSROOM_4A_CLASSWORK_URL, wait_until="domcontentloaded", timeout=7000)
                    self._wait_after_classroom_interaction(page)
                except Exception:
                    pass
                continue
            snapshot = self._snapshot_classroom_page(
                page,
                f"Google Classroom - 4-A - Trabajo de clase - Tema: {label[:70]}",
                [f"Tema/asignatura seleccionado desde Filtro por tema: {label}."],
            )
            snapshots.append(snapshot)
        if classwork_url:
            try:
                page.goto(CLASSROOM_4A_CLASSWORK_URL, wait_until="domcontentloaded", timeout=7000)
                self._wait_after_classroom_interaction(page)
            except Exception:
                pass
        return snapshots

    def _collect_classroom_topic_snapshots_by_url(
        self,
        page: Any,
        topic_links: list[dict[str, str]],
        classwork_url: str,
    ) -> list[PageSnapshot]:
        snapshots: list[PageSnapshot] = []
        for index, topic in enumerate(topic_links[:MAX_CLASSROOM_TOPIC_VIEWS], start=1):
            label = clean_text(str(topic.get("label") or "Tema Classroom")).replace("\n", " ")
            url = str(topic.get("url") or "").strip()
            if not url:
                continue
            step_label = f"Classroom: abrir URL tema {index}/{len(topic_links)} - {label}"
            step_started = self._progress(step_label)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=10000)
                self._wait_after_classroom_interaction(page)
                opened = self._is_classroom_4a_topic_page(page)
            except Exception:
                opened = False
            current_url = getattr(page, "url", "") or ""
            self._progress_done(
                step_label,
                step_started,
                f"abierto={'si' if opened else 'no'} url={current_url or 'sin url'}",
            )
            if not opened or not self._is_classroom_4a_topic_page(page):
                snapshots.append(
                    self._message_snapshot(
                        f"Google Classroom - 4-A - Trabajo en clase - Tema: {label[:70]}",
                        current_url,
                        "error",
                        [
                            f"URL de tema rechazada o redirigida fuera del curso 4-A: {url}",
                            "No se leyo esta vista para evitar mezclar rutas globales de Classroom.",
                        ],
                    )
                )
                try:
                    page.goto(CLASSROOM_4A_CLASSWORK_URL, wait_until="domcontentloaded", timeout=7000)
                    self._wait_after_classroom_interaction(page)
                except Exception:
                    pass
                continue
            snapshot = self._snapshot_classroom_page(
                page,
                f"Google Classroom - 4-A - Trabajo en clase - Tema: {label[:70]}",
                [f"Vista por tema/asignatura de Classroom: {label}."],
            )
            snapshots.append(snapshot)
        if classwork_url:
            try:
                page.goto(CLASSROOM_4A_CLASSWORK_URL, wait_until="domcontentloaded", timeout=7000)
                self._wait_after_classroom_interaction(page)
            except Exception:
                pass
        return snapshots

    def _snapshot_classroom_page(self, page: Any, platform: str, extra_notes: list[str] | None = None) -> PageSnapshot:
        step_started = self._progress(f"Classroom: leer texto visible - {platform}")
        notes = list(extra_notes or [])
        title = ""
        url = getattr(page, "url", "") or ""
        status = "ok"
        try:
            title = page.title()
        except Exception:
            title = "(sin titulo)"
            notes.append("No pude leer el titulo de la pestana.")

        try:
            page.wait_for_load_state("domcontentloaded", timeout=2500)
        except Exception:
            notes.append("La pagina no reporto carga DOM completa; usare texto visible disponible.")

        expanded_items = self._expand_classroom_visible_items(page)
        if expanded_items:
            notes.append(f"Expandi {expanded_items} bloques visibles de Classroom.")
            self._wait_after_classroom_interaction(page)

        classroom_detail_text = ""
        classroom_detail_stats: dict[str, Any] = {}
        classroom_attachment_hrefs: set[str] | None = None
        if platform.startswith("Google Classroom") and self._is_classroom_allowed_page(page):
            classroom_detail_text, classroom_detail_stats = self._extract_relevant_classroom_post_details(page, platform)
            detail_hrefs = classroom_detail_stats.get("attachment_hrefs", [])
            if classroom_detail_stats.get("candidates_seen", 0):
                classroom_attachment_hrefs = set(str(href) for href in detail_hrefs if href)
            if classroom_detail_text:
                notes.append("Abri o capture posts recientes de Classroom con titulos de pruebas/tareas y extraje su detalle.")
            candidate_titles = classroom_detail_stats.get("candidate_titles", [])
            if candidate_titles:
                notes.append("Titulos Classroom relevantes vistos: " + " | ".join(str(title) for title in candidate_titles[:8]))

        try:
            text, stats = self._extract_classroom_page_text_fast(page)
        except Exception:
            text = ""
            stats = {"frames_seen": 0, "frame_errors": 0, "source_count": 0, "sources": []}
            status = "error"
            notes.append("No pude leer el texto visible de esta vista de Classroom.")

        if classroom_detail_stats:
            stats["classroom_detail_candidates_seen"] = classroom_detail_stats.get("candidates_seen", 0)
            stats["classroom_detail_posts_opened"] = classroom_detail_stats.get("opened", 0)
            stats["classroom_detail_posts_skipped_old"] = classroom_detail_stats.get("skipped_old", 0)
            stats["classroom_detail_posts_skipped_past"] = classroom_detail_stats.get("skipped_past", 0)
            stats["classroom_detail_failed_clicks"] = classroom_detail_stats.get("failed_clicks", 0)
            stats["classroom_detail_empty_details"] = classroom_detail_stats.get("empty_details", 0)
            stats["classroom_detail_opened_by_url"] = classroom_detail_stats.get("opened_by_url", 0)
            stats["classroom_detail_opened_by_click"] = classroom_detail_stats.get("opened_by_click", 0)
            stats["classroom_detail_opened_by_card_dom"] = classroom_detail_stats.get("opened_by_card_dom", 0)
            stats["cache_new"] = classroom_detail_stats.get("cache_new", 0)
            stats["cache_updated"] = classroom_detail_stats.get("cache_updated", 0)
            stats["cache_reused"] = classroom_detail_stats.get("cache_reused", 0)
            stats["cache_omitted"] = classroom_detail_stats.get("cache_omitted", 0)
            stats["stop_incremental"] = classroom_detail_stats.get("stop_incremental", False)
            stats["classroom_detail_isolated_candidates"] = classroom_detail_stats.get("isolated_candidates", 0)
            stats["classroom_detail_rejected_global_roots"] = classroom_detail_stats.get("rejected_global_roots", 0)
            stats["classroom_detail_incomplete_details"] = classroom_detail_stats.get("incomplete_details", 0)
            stats["classroom_detail_candidate_titles"] = classroom_detail_stats.get("candidate_titles", [])
            stats["classroom_detail_candidate_hrefs"] = classroom_detail_stats.get("candidate_detail_hrefs", [])

        if classroom_detail_text:
            text = "\n\n".join(
                [
                    classroom_detail_text,
                    "TEXTO CRUDO VISIBLE DE LA VISTA POR TEMA CLASSROOM",
                    text,
                ]
            ).strip()
            notes.append("Inclui detalles de posts relevantes de este tema/asignatura.")

        if platform.startswith("Google Classroom"):
            attachment_text, attachment_stats = self._extract_classroom_attachment_text(
                page,
                allowed_hrefs=classroom_attachment_hrefs,
            )
            if attachment_text:
                text = "\n\n".join([text, attachment_text]).strip()
                stats["classroom_attachments_seen"] = attachment_stats.get("seen", 0)
                stats["classroom_pdf_attempts"] = attachment_stats.get("pdf_attempts", 0)
                stats["classroom_pdf_texts"] = attachment_stats.get("pdf_texts", 0)
            elif attachment_stats.get("seen", 0):
                stats["classroom_attachments_seen"] = attachment_stats.get("seen", 0)
                notes.append("Detecte adjuntos visibles en Classroom, pero no pude extraer texto adicional.")

        text = clean_text(text)
        if not text:
            status = "empty" if status == "ok" else status
            notes.append("La vista de Classroom no entrego texto visible.")

        login_note = login_status_note(platform, url, text)
        if login_note:
            status = "needs_login"
            notes.append(login_note)

        stats = {
            **stats,
            "chars": len(text),
            "lines": len(text.splitlines()) if text else 0,
        }
        progress_detail_parts = [
            f"{stats.get('lines', 0)} lineas, {stats.get('chars', 0)} caracteres"
        ]
        if stats.get("classroom_detail_candidates_seen", 0):
            progress_detail_parts.append(
                "posts relevantes="
                f"{stats.get('classroom_detail_candidates_seen', 0)}; "
                f"abiertos={stats.get('classroom_detail_posts_opened', 0)}; "
                f"url={stats.get('classroom_detail_opened_by_url', 0)}; "
                f"click={stats.get('classroom_detail_opened_by_click', 0)}; "
                f"card_dom={stats.get('classroom_detail_opened_by_card_dom', 0)}; "
                f"cache={stats.get('cache_reused', 0)}; "
                f"fallas={stats.get('classroom_detail_failed_clicks', 0)}; "
                f"globales_rechazados={stats.get('classroom_detail_rejected_global_roots', 0)}"
            )
            candidate_titles = stats.get("classroom_detail_candidate_titles", [])
            if candidate_titles:
                progress_detail_parts.append(
                    "titulos=" + " | ".join(str(title) for title in candidate_titles[:3])
                )
        self._progress_done(
            f"Classroom: leer texto visible - {platform}",
            step_started,
            "; ".join(progress_detail_parts),
        )
        return PageSnapshot(
            platform=platform,
            title=title,
            url=url,
            text=text[:TEXT_LIMIT],
            status=status,
            notes=notes,
            stats=stats,
        )

    def _classroom_topic_labels_from_filter(self, page: Any) -> list[str]:
        step_started = self._progress("Classroom: leer Filtro por tema")
        labels: list[str] = []
        if not self._is_classroom_4a_listing_page(page):
            self._progress_done(
                "Classroom: leer Filtro por tema",
                step_started,
                f"omitido: ruta no permitida url={getattr(page, 'url', '') or 'sin url'}",
            )
            return labels
        try:
            clicked_filter = self._open_classroom_topic_menu(page)
        except Exception:
            clicked_filter = False
        if clicked_filter:
            self._wait_after_classroom_interaction(page, delay_ms=350)
            labels = self._read_classroom_topic_options(page)
            try:
                page.keyboard.press("Escape")
                self._wait_after_classroom_interaction(page, delay_ms=250)
            except Exception:
                pass
        self._progress_done(
            "Classroom: leer Filtro por tema",
            step_started,
            f"click={'si' if clicked_filter else 'no'} temas={len(labels)}",
        )
        return labels

    def _open_classroom_topic_from_filter(self, page: Any, label: str, classwork_url: str) -> bool:
        try:
            page.goto(CLASSROOM_4A_CLASSWORK_URL, wait_until="domcontentloaded", timeout=7000)
            self._wait_after_classroom_interaction(page)
        except Exception:
            pass
        try:
            if not self._open_classroom_topic_menu(page):
                return False
            self._wait_after_classroom_interaction(page, delay_ms=350)
            clicked = self._click_classroom_topic_option(page, label)
            if clicked:
                self._wait_after_classroom_interaction(page)
            return clicked and self._is_classroom_4a_topic_page(page)
        except Exception:
            return False

    def _open_classroom_topic_menu(self, page: Any) -> bool:
        for frame in list(getattr(page, "frames", []) or []):
            try:
                result = frame.evaluate(CLASSROOM_OPEN_TOPIC_MENU_SCRIPT)
            except Exception:
                continue
            if isinstance(result, dict) and result.get("clicked"):
                return True
        return self._click_terms(page, CLASSROOM_TOPIC_FILTER_TERMS)

    def _read_classroom_topic_options(self, page: Any) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for frame in list(getattr(page, "frames", []) or []):
            try:
                frame_labels = frame.evaluate(CLASSROOM_TOPIC_OPTIONS_SCRIPT)
            except Exception:
                frame_labels = []
            if not isinstance(frame_labels, list):
                continue
            for label_value in frame_labels:
                label = clean_classroom_topic_label(str(label_value or ""))[:140]
                key = normalize(label)
                if (
                    not key
                    or key in seen
                    or is_generic_classroom_title(label)
                    or not is_relevant_classroom_topic_label(label)
                ):
                    continue
                seen.add(key)
                labels.append(label)
                if len(labels) >= MAX_CLASSROOM_TOPIC_VIEWS:
                    return labels
        return labels

    def _click_classroom_topic_option(self, page: Any, label: str) -> bool:
        for frame in list(getattr(page, "frames", []) or []):
            try:
                result = frame.evaluate(CLASSROOM_CLICK_TOPIC_OPTION_SCRIPT, label)
            except Exception:
                continue
            if isinstance(result, dict) and result.get("clicked"):
                return True
        return False

    def _read_classroom_topic_links(self, page: Any) -> list[dict[str, str]]:
        links: list[dict[str, str]] = []
        seen: set[str] = set()
        base_url = getattr(page, "url", "") or CLASSROOM_URL
        for frame in list(getattr(page, "frames", []) or []):
            try:
                frame_links = frame.evaluate(CLASSROOM_TOPIC_LINKS_SCRIPT)
            except Exception:
                frame_links = []
            if not isinstance(frame_links, list):
                continue
            for link in frame_links:
                if not isinstance(link, dict):
                    continue
                raw_url = str(link.get("absolute_href") or link.get("href") or "").strip()
                topic_url = normalize_classroom_topic_url(raw_url, base_url)
                if not topic_url or topic_url in seen:
                    continue
                label = clean_classroom_topic_label(str(link.get("label") or ""))[:140]
                if is_generic_classroom_title(label) or not is_relevant_classroom_topic_label(label):
                    continue
                seen.add(topic_url)
                links.append({"label": label or "Tema Classroom", "url": topic_url})
                if len(links) >= MAX_CLASSROOM_TOPIC_VIEWS:
                    return links
        return links

    def _navigate_and_snapshot(
        self,
        page: Any,
        platform: str,
        terms: list[str],
        missing_note: str,
    ) -> PageSnapshot:
        notes: list[str] = []
        clicked = self._click_terms(page, terms)
        if clicked:
            self._wait_after_interaction(page)
        else:
            notes.append(missing_note)

        snapshot = self._snapshot_page(page, platform)
        snapshot.notes.extend(notes)
        return snapshot

    def _click_terms(self, page: Any, terms: list[str]) -> bool:
        return bool(self._click_terms_detail(page, terms).get("clicked"))

    def _click_terms_detail(self, page: Any, terms: list[str]) -> dict[str, Any]:
        for frame in list(getattr(page, "frames", []) or []):
            try:
                result = frame.evaluate(CLICK_TEXT_SCRIPT, terms)
            except Exception:
                continue
            if isinstance(result, dict) and result.get("clicked"):
                return result
        return {"clicked": False}

    def _page_has_any_text(self, page: Any, terms: list[str]) -> bool:
        try:
            text, _stats = self._extract_page_text(page)
        except Exception:
            return False
        normalized_text = normalize(text)
        return any(normalize(term) in normalized_text for term in terms)

    def _is_classroom_course_page(self, page: Any) -> bool:
        url = (getattr(page, "url", "") or "").lower()
        return "classroom.google.com/c/" in url or "classroom.google.com/w/" in url

    def _is_classroom_4a_classwork_page(self, page: Any) -> bool:
        url = (getattr(page, "url", "") or "").lower()
        return f"classroom.google.com/w/{CLASSROOM_4A_COURSE_ID.lower()}/t/all" in url

    def _is_classroom_4a_topic_page(self, page: Any) -> bool:
        url = (getattr(page, "url", "") or "").lower()
        return f"classroom.google.com/w/{CLASSROOM_4A_COURSE_ID.lower()}/tc/" in url

    def _is_classroom_4a_listing_page(self, page: Any) -> bool:
        return self._is_classroom_4a_classwork_page(page) or self._is_classroom_4a_topic_page(page)

    def _is_classroom_4a_detail_page(self, page: Any) -> bool:
        url = (getattr(page, "url", "") or "").lower()
        parsed = urlparse(url)
        if "classroom.google." not in parsed.netloc:
            return False
        pattern = rf"^/c/{re.escape(CLASSROOM_4A_COURSE_ID.lower())}/(?:m|a)/[^/]+/details/?$"
        return bool(re.match(pattern, parsed.path.rstrip("/") + "/"))

    def _is_classroom_blocked_global_page(self, page: Any) -> bool:
        url = (getattr(page, "url", "") or "").lower()
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if "classroom.google." not in parsed.netloc:
            return False
        if "/a/not-turned-in" in path or path.startswith("/calendar") or path in ("/h", "/s", "/ai"):
            return True
        if path.startswith("/h/") or path.startswith("/s/") or path.startswith("/ai/"):
            return True
        if path.startswith("/c/") and not self._is_classroom_4a_detail_page(page):
            return True
        return False

    def _is_classroom_allowed_page(self, page: Any) -> bool:
        return self._is_classroom_4a_listing_page(page) or self._is_classroom_4a_detail_page(page)

    def _is_classroom_global_or_wrong_page(self, page: Any) -> bool:
        return not self._is_classroom_allowed_page(page)

    def _wait_after_interaction(self, page: Any) -> None:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=7000)
        except Exception:
            pass
        time.sleep(1.2)

    def _wait_after_classroom_interaction(self, page: Any, delay_ms: int = 650) -> None:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=2500)
        except Exception:
            pass
        try:
            page.wait_for_timeout(delay_ms)
        except Exception:
            time.sleep(delay_ms / 1000)

    def _message_snapshot(self, platform: str, url: str, status: str, notes: list[str]) -> PageSnapshot:
        return PageSnapshot(
            platform=platform,
            title=platform,
            url=url,
            text="",
            status=status,
            notes=notes,
            stats={"chars": 0, "lines": 0, "source_count": 0, "frames_seen": 0, "sources": []},
        )

    def status(self) -> dict[str, Any]:
        return self._call_browser_thread(self._status_on_browser_thread)

    def _status_on_browser_thread(self) -> dict[str, Any]:
        pages: list[dict[str, str]] = []
        browser_open = self._context is not None
        try:
            if self._context is not None:
                for page in self._context.pages:
                    platform = classify_platform(getattr(page, "url", "") or "")
                    if platform:
                        pages.append(self._page_summary(page, platform))
        except Exception as exc:
            if is_browser_closed_error(exc):
                self._discard_browser_state()
                browser_open = False
                pages = []
            else:
                raise
        return {
            "browser_open": browser_open,
            "started_at": self._browser_started_at,
            "pages": pages,
            "chrome": str(self._browser_path()) if (CHROME_EXE.exists() or EDGE_EXE.exists()) else "",
        }

    def close(self) -> None:
        if threading.current_thread() is self._worker_thread:
            self._close_on_browser_thread()
            return
        worker_thread = self._worker_thread
        if worker_thread is not None and worker_thread.is_alive():
            try:
                self._call_browser_thread(self._close_on_browser_thread)
            finally:
                self._worker_queue.put(None)
                worker_thread.join(timeout=3)
                with self._lock:
                    if self._worker_thread is worker_thread:
                        self._worker_thread = None
        else:
            self._close_on_browser_thread()

    def _close_on_browser_thread(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._context = None
        self._playwright = None
        self._browser_started_at = None

    def _find_page(self, pages: list[Any], needle: str) -> Any | None:
        needle = needle.lower()
        for page in pages:
            if needle in (getattr(page, "url", "") or "").lower():
                return page
        return None

    def _snapshot_page(self, page: Any, platform: str) -> PageSnapshot:
        if platform.startswith("Google Classroom"):
            return self._snapshot_classroom_page(page, platform)

        notes: list[str] = []
        title = ""
        url = getattr(page, "url", "") or ""
        text = ""
        status = "ok"
        stats: dict[str, Any] = {}

        try:
            title = page.title()
        except Exception:
            title = "(sin titulo)"
            notes.append("No pude leer el titulo de la pestana.")

        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            notes.append("La pagina no reporto carga completa; usare el texto visible disponible.")

        try:
            page.wait_for_load_state("networkidle", timeout=7000)
        except Exception:
            pass

        try:
            page.wait_for_function(
                f"() => document.body && document.body.innerText.trim().length >= {MEANINGFUL_TEXT_MIN_CHARS}",
                timeout=7000,
            )
        except Exception:
            pass

        if platform == "SchoolNet - Calificaciones":
            expanded_rows = self._expand_schoolnet_grade_rows(page)
            if expanded_rows:
                notes.append(f"Expandi {expanded_rows} filas de calificaciones para leer detalles visibles.")
                self._wait_after_interaction(page)
        elif platform == "SchoolNet - Conducta":
            expanded_rows = self._expand_schoolnet_conducta_rows(page)
            if expanded_rows:
                notes.append(f"Expandi {expanded_rows} filas de conducta para leer detalles visibles.")
                self._wait_after_interaction(page)
        elif platform.startswith("Google Classroom"):
            expanded_items = self._expand_classroom_visible_items(page)
            if expanded_items:
                notes.append(f"Expandi {expanded_items} bloques visibles de Classroom antes de leer detalles.")
                self._wait_after_interaction(page)

        classroom_detail_text = ""
        classroom_detail_stats: dict[str, Any] = {}
        classroom_attachment_hrefs: set[str] | None = None
        if platform.startswith("Google Classroom"):
            classroom_detail_text, classroom_detail_stats = self._extract_relevant_classroom_post_details(page, platform)
            stats["classroom_detail_candidates_seen"] = classroom_detail_stats.get("candidates_seen", 0)
            stats["classroom_detail_posts_opened"] = classroom_detail_stats.get("opened", 0)
            stats["classroom_detail_posts_skipped_old"] = classroom_detail_stats.get("skipped_old", 0)
            stats["classroom_detail_posts_skipped_past"] = classroom_detail_stats.get("skipped_past", 0)
            stats["classroom_detail_failed_clicks"] = classroom_detail_stats.get("failed_clicks", 0)
            stats["classroom_detail_empty_details"] = classroom_detail_stats.get("empty_details", 0)
            stats["classroom_detail_opened_by_url"] = classroom_detail_stats.get("opened_by_url", 0)
            stats["classroom_detail_opened_by_click"] = classroom_detail_stats.get("opened_by_click", 0)
            stats["classroom_detail_opened_by_card_dom"] = classroom_detail_stats.get("opened_by_card_dom", 0)
            stats["classroom_detail_candidate_titles"] = classroom_detail_stats.get("candidate_titles", [])
            stats["classroom_detail_candidate_hrefs"] = classroom_detail_stats.get("candidate_detail_hrefs", [])
            detail_hrefs = classroom_detail_stats.get("attachment_hrefs", [])
            if classroom_detail_stats.get("candidates_seen", 0):
                classroom_attachment_hrefs = set(str(href) for href in detail_hrefs if href)
            if classroom_detail_text:
                notes.append("Abri posts recientes de Classroom con titulos de pruebas/tareas y extraje su detalle.")
            candidate_titles = classroom_detail_stats.get("candidate_titles", [])
            if candidate_titles:
                notes.append("Titulos Classroom relevantes vistos: " + " | ".join(str(title) for title in candidate_titles[:8]))

        try:
            text, stats = self._extract_page_text(page)
        except Exception:
            status = "error"
            notes.append("No pude leer el texto visible de esta pestana.")

        if classroom_detail_stats:
            stats["classroom_detail_candidates_seen"] = classroom_detail_stats.get("candidates_seen", 0)
            stats["classroom_detail_posts_opened"] = classroom_detail_stats.get("opened", 0)
            stats["classroom_detail_posts_skipped_old"] = classroom_detail_stats.get("skipped_old", 0)
            stats["classroom_detail_posts_skipped_past"] = classroom_detail_stats.get("skipped_past", 0)
            stats["classroom_detail_failed_clicks"] = classroom_detail_stats.get("failed_clicks", 0)
            stats["classroom_detail_empty_details"] = classroom_detail_stats.get("empty_details", 0)
            stats["classroom_detail_opened_by_url"] = classroom_detail_stats.get("opened_by_url", 0)
            stats["classroom_detail_opened_by_click"] = classroom_detail_stats.get("opened_by_click", 0)
            stats["classroom_detail_candidate_titles"] = classroom_detail_stats.get("candidate_titles", [])
            stats["classroom_detail_candidate_hrefs"] = classroom_detail_stats.get("candidate_detail_hrefs", [])

        if classroom_detail_text:
            text = "\n\n".join(
                [
                    classroom_detail_text,
                    "TEXTO CRUDO VISIBLE DE LA SECCION CLASSROOM",
                    text,
                ]
            ).strip()

        if platform.startswith("Google Classroom"):
            attachment_text, attachment_stats = self._extract_classroom_attachment_text(
                page,
                allowed_hrefs=classroom_attachment_hrefs,
            )
            if attachment_text:
                text = "\n\n".join(
                    [
                        text,
                        attachment_text,
                    ]
                ).strip()
                stats["classroom_attachments_seen"] = attachment_stats.get("seen", 0)
                stats["classroom_pdf_attempts"] = attachment_stats.get("pdf_attempts", 0)
                stats["classroom_pdf_texts"] = attachment_stats.get("pdf_texts", 0)
            elif attachment_stats.get("seen", 0):
                stats["classroom_attachments_seen"] = attachment_stats.get("seen", 0)
                notes.append("Detecte adjuntos visibles en Classroom, pero no pude extraer texto adicional.")

        if platform == "SchoolNet - Calificaciones":
            structured_grades, structured_stats = self._extract_schoolnet_grades_table(page)
            if structured_grades:
                text = "\n\n".join(
                    [
                        structured_grades,
                        "TEXTO CRUDO VISIBLE",
                        text,
                    ]
                ).strip()
                stats["structured_grade_sources"] = structured_stats.get("source_count", 0)
            else:
                notes.append("No pude construir una lectura estructurada de la tabla de calificaciones P1.")
        elif platform == "SchoolNet - Conducta":
            structured_conducta, structured_stats = self._extract_schoolnet_conducta_views(page)
            if structured_stats.get("conducta_view_clicks") is not None:
                stats["conducta_view_clicks"] = structured_stats.get("conducta_view_clicks", 0)
                stats["conducta_view_failures"] = structured_stats.get("conducta_view_failures", 0)
                stats["conducta_views_captured"] = structured_stats.get("views_captured", 0)
            for view_note in structured_stats.get("notes", []) or []:
                notes.append(str(view_note))
            if not structured_conducta:
                structured_conducta, structured_stats = self._extract_schoolnet_conducta_detail(page)
            blocks: list[str] = []
            if structured_conducta:
                blocks.append(structured_conducta)
                stats["structured_conducta_sources"] = structured_stats.get("source_count", 0)
            else:
                notes.append("No pude construir una lectura estructurada de conducta; incluire el texto crudo visible.")
            if text:
                blocks.extend(["LECTURA CRUDA COMPLETA SCHOOLNET CONDUCTA", text])
            text = "\n\n".join(blocks).strip()

        text = clean_text(text)
        if not text:
            status = "empty"
            notes.append("La pestana no entrego texto visible.")
        elif stats.get("source_count", 0) == 1 and stats.get("frames_seen", 0) > 1:
            notes.append("Solo pude leer texto del frame principal; algunos iframes no entregaron texto.")

        login_note = login_status_note(platform, url, text)
        if login_note:
            status = "needs_login"
            notes.append(login_note)

        return PageSnapshot(
            platform=platform,
            title=title,
            url=url,
            text=text[:TEXT_LIMIT],
            status=status,
            notes=notes,
            stats={
                **stats,
                "chars": len(text),
                "lines": len(text.splitlines()) if text else 0,
            },
        )

    def _page_summary(self, page: Any, platform: str) -> dict[str, str]:
        try:
            title = page.title()
        except Exception:
            title = "(sin titulo)"
        return {"platform": platform, "title": title, "url": getattr(page, "url", "") or ""}

    def _extract_classroom_page_text_fast(self, page: Any) -> tuple[str, dict[str, Any]]:
        parts: list[str] = []
        sources: list[dict[str, Any]] = []
        frame_errors = 0
        frames = list(getattr(page, "frames", []) or [])
        for index, frame in enumerate(frames):
            frame_label = "main" if index == 0 else f"iframe {index}"
            frame_url = getattr(frame, "url", "") or ""
            try:
                body_text = frame.evaluate("() => document.body ? document.body.innerText : ''")
                body_text = clean_text(str(body_text or ""))
            except Exception:
                body_text = ""
                frame_errors += 1
            if body_text:
                parts.append(body_text)
                sources.append(
                    {
                        "source": frame_label,
                        "kind": "visible_text_fast",
                        "chars": len(body_text),
                        "url": frame_url[:180],
                    }
                )

            try:
                attr_text = frame.evaluate(ATTRIBUTE_TEXT_SCRIPT)
                attr_text = clean_text(str(attr_text or ""))
            except Exception:
                attr_text = ""
            if attr_text:
                parts.append(attr_text)
                sources.append(
                    {
                        "source": frame_label,
                        "kind": "labels",
                        "chars": len(attr_text),
                        "url": frame_url[:180],
                    }
                )

        merged = merge_text_parts(parts)
        return merged, {
            "frames_seen": len(frames),
            "frame_errors": frame_errors,
            "source_count": len(sources),
            "sources": sources[:12],
        }

    def _extract_page_text(self, page: Any) -> tuple[str, dict[str, Any]]:
        parts: list[str] = []
        sources: list[dict[str, Any]] = []
        frame_errors = 0
        frames = list(getattr(page, "frames", []) or [])

        for index, frame in enumerate(frames):
            frame_label = "main" if index == 0 else f"iframe {index}"
            frame_url = getattr(frame, "url", "") or ""

            try:
                body_text = frame.locator("body").inner_text(timeout=5000 if index == 0 else 2500)
                body_text = clean_text(body_text)
            except Exception:
                body_text = ""
                frame_errors += 1

            if body_text:
                parts.append(body_text)
                sources.append(
                    {
                        "source": frame_label,
                        "kind": "visible_text",
                        "chars": len(body_text),
                        "url": frame_url[:180],
                    }
                )

            try:
                attr_text = frame.evaluate(ATTRIBUTE_TEXT_SCRIPT)
                attr_text = clean_text(str(attr_text or ""))
            except Exception:
                attr_text = ""

            if attr_text:
                parts.append(attr_text)
                sources.append(
                    {
                        "source": frame_label,
                        "kind": "labels",
                        "chars": len(attr_text),
                        "url": frame_url[:180],
                    }
                )

        merged = merge_text_parts(parts)
        return merged, {
            "frames_seen": len(frames),
            "frame_errors": frame_errors,
            "source_count": len(sources),
            "sources": sources[:12],
        }

    def _extract_relevant_classroom_post_details(self, page: Any, platform: str) -> tuple[str, dict[str, Any]]:
        today = current_report_date()
        opened = 0
        skipped_old = 0
        skipped_past = 0
        failed_clicks = 0
        empty_details = 0
        opened_by_url = 0
        opened_by_click = 0
        opened_by_card_dom = 0
        cache_new = 0
        cache_updated = 0
        cache_reused = 0
        cache_omitted = 0
        stop_incremental = False
        consecutive_known = 0
        candidates_seen = 0
        isolated_candidates = 0
        rejected_global_roots = 0
        incomplete_details = 0
        candidate_titles: list[str] = []
        candidate_detail_hrefs: list[str] = []
        detail_blocks: list[str] = []
        attachment_hrefs: list[str] = []
        attempted_keys: set[str] = set()
        reused_cache_record_ids: set[str] = set()
        visible_fallback_text = ""
        visible_fallback_stats: dict[str, Any] = {}
        section_terms = CLASSROOM_CLASSWORK_TERMS
        current_detail = self._extract_current_open_classroom_detail(page, platform, today)
        if current_detail:
            detail_text, current_hrefs = current_detail
            return detail_text, {
                "candidates_seen": 1,
                "opened": 1,
                "skipped_old": 0,
                "skipped_past": 0,
                "failed_clicks": 0,
                "empty_details": 0,
                "opened_by_url": 0,
                "opened_by_click": 0,
                "opened_by_card_dom": 0,
                "cache_new": 0,
                "cache_updated": 0,
                "cache_reused": 0,
                "cache_omitted": 0,
                "stop_incremental": False,
                "isolated_candidates": 1,
                "rejected_global_roots": 0,
                "incomplete_details": 0,
                "candidate_titles": [detect_relevant_classroom_title(detail_text)] if detect_relevant_classroom_title(detail_text) else [],
                "candidate_detail_hrefs": [],
                "attachment_hrefs": current_hrefs,
            }

        visible_text, visible_stats = self._extract_visible_relevant_classroom_blocks(page, platform, today)
        if visible_text:
            visible_fallback_text = visible_text
            visible_fallback_stats = visible_stats
            skipped_old += int(visible_stats.get("skipped_old", 0) or 0)
            skipped_past += int(visible_stats.get("skipped_past", 0) or 0)
            candidates_seen = max(candidates_seen, int(visible_stats.get("seen", 0) or 0))
            candidate_titles.extend(str(title) for title in visible_stats.get("candidate_titles", []) if title)
            attachment_hrefs.extend(str(href) for href in visible_stats.get("attachment_hrefs", []) if href)

        scroll_attempts = 0
        while opened < MAX_CLASSROOM_POSTS_TO_OPEN:
            if stop_incremental:
                break
            candidates = self._classroom_post_candidates(page)
            candidates_seen = max(candidates_seen, len(candidates))
            for candidate in candidates:
                title = clean_text(str(candidate.get("title") or "")).replace("\n", " ")
                if title:
                    candidate_titles.append(title)
                if bool(candidate.get("isolated", True)):
                    isolated_candidates += 1
                else:
                    rejected_global_roots += 1
                detail_href = classroom_detail_href(candidate, getattr(page, "url", "") or CLASSROOM_URL)
                if detail_href:
                    candidate_detail_hrefs.append(detail_href)
            selected: dict[str, Any] | None = None

            for candidate in candidates:
                base_key = normalize(
                    f"{candidate.get('title', '')}|{candidate.get('posted', '')}"
                )
                key = normalize(
                    f"{candidate.get('title', '')}|{candidate.get('posted', '')}|{candidate.get('detail_href', '')}|{candidate.get('href', '')}"
                )
                if not key or key in attempted_keys or base_key in attempted_keys:
                    continue
                skip_reason = classroom_candidate_skip_reason(candidate, today)
                if skip_reason == "old":
                    attempted_keys.add(key)
                    skipped_old += 1
                    continue
                if skip_reason == "past":
                    attempted_keys.add(key)
                    skipped_past += 1
                    continue
                if skip_reason:
                    attempted_keys.add(key)
                    continue
                if self._evidence_store is not None and not self._force_full_scan:
                    cached_record = self._cached_classroom_record(candidate, platform)
                    if cached_record:
                        attempted_keys.add(key)
                        if base_key:
                            attempted_keys.add(base_key)
                        cached_record_id = str(cached_record.get("id") or "")
                        if cached_record_id and cached_record_id in reused_cache_record_ids:
                            continue
                        if cached_record_id:
                            reused_cache_record_ids.add(cached_record_id)
                        cache_reused += 1
                        cache_omitted += 1
                        consecutive_known += 1
                        detail_blocks.extend(
                            self._classroom_cached_detail_block(
                                cached_record,
                                platform,
                                cache_reused,
                            )
                        )
                        if consecutive_known >= INCREMENTAL_KNOWN_STOP_COUNT:
                            stop_incremental = True
                            break
                        continue
                consecutive_known = 0
                if not bool(candidate.get("isolated", True)) and not classroom_detail_href(candidate, getattr(page, "url", "") or CLASSROOM_URL):
                    attempted_keys.add(key)
                    rejected_global_roots += 1
                    continue
                selected = candidate
                break

            if opened >= MAX_CLASSROOM_POSTS_TO_OPEN:
                break
            if selected is None:
                if scroll_attempts >= 4:
                    break
                moved = self._scroll_classroom_listing(page)
                scroll_attempts += 1
                if not moved:
                    break
                continue

            original_url = getattr(page, "url", "") or ""
            token = str(selected.get("token") or "")
            selected_key = normalize(
                f"{selected.get('title', '')}|{selected.get('posted', '')}|{selected.get('detail_href', '')}|{selected.get('href', '')}"
            )
            selected_base_key = normalize(f"{selected.get('title', '')}|{selected.get('posted', '')}")
            if selected_key:
                attempted_keys.add(selected_key)
            if selected_base_key:
                attempted_keys.add(selected_base_key)
            detail_href = classroom_detail_href(selected, original_url or CLASSROOM_URL)
            opened_method = ""
            expanded_detail_text = ""
            expanded_detail_links: list[dict[str, str]] = []
            clicked = False
            card_dom_detail = visible_classroom_candidate_detail(
                {
                    **selected,
                    "preview": selected.get("body_text") or selected.get("preview") or "",
                    "isolated": True,
                }
            )
            if bool(selected.get("is_structured_card")) and card_dom_detail:
                opened += 1
                opened_by_card_dom += 1
                detail_dates = extract_date_mentions(
                    "\n".join(
                        [
                            str(selected.get("title") or ""),
                            str(selected.get("posted") or ""),
                            str(selected.get("due") or ""),
                            card_dom_detail,
                        ]
                    ),
                    today,
                )
                links = selected.get("links", [])
                if isinstance(links, list):
                    for link in links:
                        if not isinstance(link, dict):
                            continue
                        attachment_hrefs.append(str(link.get("href") or ""))
                        attachment_hrefs.append(str(link.get("absolute_href") or ""))
                detail_blocks.extend(
                    [
                        "",
                        f"POST CAPTURADO DESDE TARJETA FILTRADA {opened}",
                        f"Fuente: {platform}",
                        "Metodo de captura: card_dom",
                        f"Tipo Classroom: {selected.get('kind', 'No detectado') or 'No detectado'}",
                        f"Asignatura/tema detectado: {selected.get('subject', 'No detectado') or 'No detectado'}",
                        f"Titulo: {selected.get('title', 'No detectado') or 'No detectado'}",
                        f"URL detalle detectada: {detail_href or 'No detectada'}",
                        f"Publicado/fecha visible en tarjeta: {selected.get('posted', 'No detectada') or 'No detectada'}",
                        f"Fecha de entrega visible: {selected.get('due', 'No detectada') or 'No detectada'}",
                        f"Fechas detectadas en tarjeta: {format_date_mentions(detail_dates) or 'No detectadas'}",
                        "Texto extraido desde tarjeta filtrada:",
                        card_dom_detail[:4200],
                    ]
                )
                if isinstance(links, list) and links:
                    detail_blocks.append("Links visibles en la tarjeta filtrada:")
                    for link in links[:10]:
                        if not isinstance(link, dict):
                            continue
                        detail_blocks.append(
                            f"- {link.get('label') or '(sin etiqueta)'} | URL: {link.get('href') or link.get('absolute_href') or '(sin URL)'}"
                        )
                cache_result = self._cache_classroom_detail(
                    selected,
                    platform,
                    card_dom_detail,
                    detail_href,
                    "card_dom",
                )
                if cache_result == "new":
                    cache_new += 1
                elif cache_result == "updated":
                    cache_updated += 1
                continue
            if detail_href:
                try:
                    page.goto(detail_href, wait_until="domcontentloaded", timeout=10000)
                    self._wait_after_interaction(page)
                    navigated_detail = self._extract_classroom_detail_from_current_page(page, selected)
                    if navigated_detail:
                        expanded_detail_text, expanded_detail_links = navigated_detail
                        opened_method = "url"
                        opened_by_url += 1
                    elif self._wait_for_classroom_detail_open(page, selected):
                        opened_method = "url"
                        opened_by_url += 1
                except Exception:
                    navigated_detail = self._extract_classroom_detail_from_current_page(page, selected)
                    if navigated_detail:
                        expanded_detail_text, expanded_detail_links = navigated_detail
                        opened_method = "url_after_error"
                        opened_by_url += 1
                    else:
                        opened_method = ""
            if not opened_method:
                title_dom_detail = self._extract_classroom_detail_by_title(page, selected)
                if title_dom_detail:
                    expanded_detail_text, expanded_detail_links = title_dom_detail
                    if expanded_detail_text:
                        opened_method = "title_dom"
                        opened_by_click += 1

            if not opened_method:
                try:
                    frames = list(getattr(page, "frames", []) or [])
                    frame_index = int(selected.get("frame_index", 0) or 0)
                    target_frame = frames[frame_index] if 0 <= frame_index < len(frames) else page
                    result = target_frame.evaluate(CLASSROOM_CLICK_POST_CANDIDATE_SCRIPT, token)
                    clicked = isinstance(result, dict) and bool(result.get("clicked"))
                except Exception:
                    clicked = False
                if not clicked:
                    clicked = self._open_classroom_post_by_title(page, selected)
                if clicked:
                    self._wait_after_interaction(page)
                    if self._wait_for_classroom_detail_open(page, selected):
                        opened_method = "click"
                        opened_by_click += 1
                    else:
                        title_dom_detail = self._extract_classroom_detail_by_title(page, selected)
                        if title_dom_detail:
                            expanded_detail_text, expanded_detail_links = title_dom_detail
                            if expanded_detail_text:
                                opened_method = "click_expand"
                                opened_by_click += 1

            if not opened_method:
                failed_clicks += 1
                visible_detail = visible_classroom_candidate_detail(selected)
                if visible_detail:
                    opened += 1
                    detail_dates = extract_date_mentions(
                        "\n".join(
                            [
                                str(selected.get("title") or ""),
                                str(selected.get("posted") or ""),
                                visible_detail,
                            ]
                        ),
                        today,
                    )
                    detail_blocks.extend(
                        [
                            "",
                            f"POST VISIBLE POR FALLA DE CLICK {opened}",
                            f"Fuente: {platform}",
                            f"Asignatura/tema detectado: {selected.get('subject', 'No detectado') or 'No detectado'}",
                            f"Titulo: {selected.get('title', 'No detectado') or 'No detectado'}",
                            f"URL detalle detectada: {detail_href or 'No detectada'}",
                            f"Publicado/fecha visible en tarjeta: {selected.get('posted', 'No detectada') or 'No detectada'}",
                            f"Fechas detectadas en detalle: {format_date_mentions(detail_dates) or 'No detectadas'}",
                            "Texto visible extraido del post:",
                            visible_detail[:3600],
                        ]
                    )
                    cache_result = self._cache_classroom_detail(
                        selected,
                        platform,
                        visible_detail,
                        detail_href,
                        "visible_fallback",
                    )
                    if cache_result == "new":
                        cache_new += 1
                    elif cache_result == "updated":
                        cache_updated += 1
                else:
                    empty_details += 1
                    incomplete_details += 1
                self._return_from_classroom_detail(page, original_url, section_terms)
                continue

            if expanded_detail_text:
                detail_text = expanded_detail_text
                detail_links = expanded_detail_links
            else:
                detail_text = self._extract_scrollable_classroom_detail_text(page)
                detail_text = compact_classroom_detail_text(detail_text, selected)
                detail_links = self._extract_visible_classroom_links(page)
            visible_detail = visible_classroom_candidate_detail(selected)
            if len(detail_text) < 120 and visible_detail:
                empty_details += 1
                detail_text = visible_detail
            attachment_hrefs.extend(link.get("href", "") for link in detail_links if link.get("href"))
            attachment_hrefs.extend(link.get("absolute_href", "") for link in detail_links if link.get("absolute_href"))

            opened += 1
            if not detail_text:
                empty_details += 1
            detail_dates = extract_date_mentions(detail_text, today)
            detail_blocks.extend(
                [
                    "",
                    f"POST ABIERTO {opened}",
                    f"Fuente: {platform}",
                    f"Metodo de apertura: {opened_method}",
                    f"Asignatura/tema detectado: {selected.get('subject', 'No detectado') or 'No detectado'}",
                    f"Titulo: {selected.get('title', 'No detectado') or 'No detectado'}",
                    f"URL detalle detectada: {detail_href or 'No detectada'}",
                    f"Publicado/fecha visible en tarjeta: {selected.get('posted', 'No detectada') or 'No detectada'}",
                    f"Fechas detectadas en detalle: {format_date_mentions(detail_dates) or 'No detectadas'}",
                    "Texto extraido al abrir el post:",
                    detail_text[:3600] if detail_text else "No se detecto texto adicional al abrir el post.",
                ]
            )
            if detail_links:
                detail_blocks.append("Links visibles en el post abierto:")
                for link in detail_links[:10]:
                    detail_blocks.append(
                        f"- {link.get('label') or '(sin etiqueta)'} | URL: {link.get('href') or link.get('absolute_href') or '(sin URL)'}"
                    )
            cache_result = self._cache_classroom_detail(
                selected,
                platform,
                detail_text,
                detail_href,
                opened_method,
            )
            if cache_result == "new":
                cache_new += 1
            elif cache_result == "updated":
                cache_updated += 1

            self._return_from_classroom_detail(page, original_url, section_terms)

        if not detail_blocks and visible_fallback_text:
            detail_blocks.append(visible_fallback_text)
            opened += int(visible_fallback_stats.get("captured", 0) or 0)

        if not detail_blocks:
            return "", {
                "candidates_seen": candidates_seen,
                "opened": opened,
                "skipped_old": skipped_old,
                "skipped_past": skipped_past,
                "failed_clicks": failed_clicks,
                "empty_details": empty_details,
                "opened_by_url": opened_by_url,
                "opened_by_click": opened_by_click,
                "opened_by_card_dom": opened_by_card_dom,
                "cache_new": cache_new,
                "cache_updated": cache_updated,
                "cache_reused": cache_reused,
                "cache_omitted": cache_omitted,
                "stop_incremental": stop_incremental,
                "isolated_candidates": isolated_candidates,
                "rejected_global_roots": rejected_global_roots,
                "incomplete_details": incomplete_details,
                "candidate_titles": dedupe(candidate_titles),
                "candidate_detail_hrefs": dedupe(candidate_detail_hrefs),
                "attachment_hrefs": [],
            }

        header = [
            f"DETALLE DE POSTS RELEVANTES ABIERTOS EN {platform.upper()}",
            (
                "Criterio: se abrieron solo tarjetas recientes (maximo 31 dias si la fecha de publicacion "
                "es visible) cuyo titulo contiene palabras de prueba/evaluacion/tarea/material."
            ),
            f"Fecha de corte: {today.isoformat()}",
        ]
        return "\n".join(header + detail_blocks).strip(), {
            "candidates_seen": candidates_seen,
            "opened": opened,
            "skipped_old": skipped_old,
            "skipped_past": skipped_past,
            "failed_clicks": failed_clicks,
            "empty_details": empty_details,
            "opened_by_url": opened_by_url,
            "opened_by_click": opened_by_click,
            "opened_by_card_dom": opened_by_card_dom,
            "cache_new": cache_new,
            "cache_updated": cache_updated,
            "cache_reused": cache_reused,
            "cache_omitted": cache_omitted,
            "stop_incremental": stop_incremental,
            "isolated_candidates": isolated_candidates,
            "rejected_global_roots": rejected_global_roots,
            "incomplete_details": incomplete_details,
            "candidate_titles": dedupe(candidate_titles),
            "candidate_detail_hrefs": dedupe(candidate_detail_hrefs),
            "attachment_hrefs": dedupe(attachment_hrefs),
        }

    def _extract_visible_relevant_classroom_blocks(
        self,
        page: Any,
        platform: str,
        today: dt.date,
    ) -> tuple[str, dict[str, Any]]:
        raw_blocks: list[dict[str, Any]] = []
        for frame in list(getattr(page, "frames", []) or []):
            try:
                frame_blocks = frame.evaluate(
                    CLASSROOM_VISIBLE_RELEVANT_BLOCKS_SCRIPT,
                    CLASSROOM_POST_TITLE_KEYWORDS,
                )
            except Exception:
                frame_blocks = []
            if isinstance(frame_blocks, list):
                raw_blocks.extend(block for block in frame_blocks if isinstance(block, dict))

        blocks: list[str] = []
        attachment_hrefs: list[str] = []
        attempted_keys: list[str] = []
        candidate_titles: list[str] = []
        skipped_old = 0
        skipped_past = 0
        captured = 0

        for block in raw_blocks[:MAX_CLASSROOM_POSTS_TO_OPEN]:
            candidate = {
                "title": clean_text(str(block.get("title") or "")),
                "subject": clean_text(str(block.get("subject") or "")),
                "posted": clean_text(str(block.get("posted") or "")),
                "preview": clean_text(str(block.get("text") or "")),
            }
            key = normalize(f"{candidate['title']}|{candidate['posted']}")
            if key:
                attempted_keys.append(key)
            if candidate["title"]:
                candidate_titles.append(candidate["title"])
            skip_reason = classroom_candidate_skip_reason(candidate, today)
            if skip_reason == "old":
                skipped_old += 1
                continue
            if skip_reason == "past":
                skipped_past += 1
                continue
            if skip_reason:
                continue

            detail = visible_classroom_candidate_detail(candidate)
            if not detail:
                continue

            captured += 1
            dates = extract_date_mentions("\n".join([candidate["title"], candidate["posted"], detail]), today)
            blocks.extend(
                [
                    "",
                    f"POST VISIBLE {captured}",
                    f"Fuente: {platform}",
                    f"Asignatura/tema detectado: {candidate['subject'] or 'No detectado'}",
                    f"Titulo: {candidate['title'] or 'No detectado'}",
                    f"Publicado/fecha visible en tarjeta: {candidate['posted'] or 'No detectada'}",
                    f"Fechas detectadas en detalle: {format_date_mentions(dates) or 'No detectadas'}",
                    "Texto visible extraido del post:",
                    detail[:3600],
                ]
            )

            links = block.get("links", [])
            if isinstance(links, list) and links:
                blocks.append("Links visibles en el post:")
                for link in links[:10]:
                    if not isinstance(link, dict):
                        continue
                    label = clean_text(str(link.get("label") or "")).replace("\n", " ")
                    href = str(link.get("href") or "").strip()
                    blocks.append(f"- {label or '(sin etiqueta)'} | URL: {href or '(sin URL)'}")
                    if href:
                        attachment_hrefs.append(href)
                        attachment_hrefs.append(urljoin(getattr(page, "url", "") or CLASSROOM_URL, href))

        if not blocks:
            return "", {
                "seen": len(raw_blocks),
                "captured": 0,
                "skipped_old": skipped_old,
                "skipped_past": skipped_past,
                "attachment_hrefs": [],
                "attempted_keys": attempted_keys,
                "candidate_titles": dedupe(candidate_titles),
            }

        header = [
            f"POSTS RELEVANTES VISIBLES EN {platform.upper()}",
            "Criterio: bloques ya desplegados en Classroom cuyo titulo contiene palabras de prueba/evaluacion/tarea/test.",
        ]
        return "\n".join(header + blocks).strip(), {
            "seen": len(raw_blocks),
            "captured": captured,
            "skipped_old": skipped_old,
            "skipped_past": skipped_past,
            "attachment_hrefs": dedupe(attachment_hrefs),
            "attempted_keys": dedupe(attempted_keys),
            "candidate_titles": dedupe(candidate_titles),
        }

    def _extract_current_open_classroom_detail(
        self,
        page: Any,
        platform: str,
        today: dt.date,
    ) -> tuple[str, list[str]] | None:
        current_url = (getattr(page, "url", "") or "").lower()
        if "/details" not in current_url:
            return None
        try:
            current_text, _stats = self._extract_page_text(page)
        except Exception:
            return None
        if not looks_like_open_classroom_detail(current_text):
            return None
        title = detect_relevant_classroom_title(current_text)
        if not title:
            return None
        candidate = {"title": title, "posted": extract_classroom_posted_line(current_text), "preview": current_text[:1200]}
        if classroom_candidate_skip_reason(candidate, today):
            return None

        detail_text = self._extract_scrollable_classroom_detail_text(page)
        detail_text = compact_classroom_detail_text(detail_text, candidate)
        detail_links = self._extract_visible_classroom_links(page)
        hrefs = []
        hrefs.extend(link.get("href", "") for link in detail_links if link.get("href"))
        hrefs.extend(link.get("absolute_href", "") for link in detail_links if link.get("absolute_href"))
        detail_dates = extract_date_mentions(detail_text, today)
        subject = extract_subject(detail_text) or extract_subject(current_text)
        block_lines = [
            f"DETALLE DE POST RELEVANTE ABIERTO EN {platform.upper()}",
            "Criterio: el post ya estaba abierto y el titulo contiene palabras de prueba/evaluacion/tarea/material.",
            f"Fecha de corte: {today.isoformat()}",
            "",
            "POST ABIERTO 1",
            f"Fuente: {platform}",
            f"Asignatura/tema detectado: {subject or 'No detectado'}",
            f"Titulo: {title}",
            f"Publicado/fecha visible: {candidate.get('posted') or 'No detectada'}",
            f"Fechas detectadas en detalle: {format_date_mentions(detail_dates) or 'No detectadas'}",
            "Texto extraido al abrir el post:",
            detail_text[:3600] if detail_text else "No se detecto texto adicional al abrir el post.",
        ]
        if detail_links:
            block_lines.append("Links visibles en el post abierto:")
            for link in detail_links[:10]:
                block_lines.append(
                    f"- {link.get('label') or '(sin etiqueta)'} | URL: {link.get('href') or link.get('absolute_href') or '(sin URL)'}"
                )
        return "\n".join(block_lines).strip(), dedupe(hrefs)

    def _classroom_post_candidates(self, page: Any) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for frame_index, frame in enumerate(list(getattr(page, "frames", []) or [])):
            frame_candidate_groups: list[list[dict[str, Any]]] = []
            for script in (
                CLASSROOM_STRUCTURED_TOPIC_CARDS_SCRIPT,
                CLASSROOM_RELEVANT_POST_CANDIDATES_SCRIPT,
            ):
                try:
                    frame_candidates = frame.evaluate(script, CLASSROOM_POST_TITLE_KEYWORDS)
                except Exception:
                    frame_candidates = []
                if isinstance(frame_candidates, list):
                    frame_candidate_groups.append(
                        [candidate for candidate in frame_candidates if isinstance(candidate, dict)]
                    )
            for frame_candidates in frame_candidate_groups:
                for candidate in frame_candidates:
                    title = clean_text(str(candidate.get("title") or "")).replace("\n", " ")
                    posted = clean_text(str(candidate.get("posted") or "")).replace("\n", " ")
                    href = str(candidate.get("href") or "").strip()
                    detail_href = classroom_detail_href(candidate, getattr(page, "url", "") or CLASSROOM_URL)
                    body_text = clean_text(str(candidate.get("body_text") or candidate.get("text") or ""))
                    preview = clean_text(str(candidate.get("preview") or body_text or ""))
                    key = normalize(
                        "|".join(
                            [
                                title,
                                posted,
                                detail_href,
                                href,
                                str(candidate.get("data_stream_item_id") or ""),
                            ]
                        )
                    )
                    if not title or key in seen_keys:
                        continue
                    seen_keys.add(key)
                    candidate["title"] = title
                    candidate["kind"] = clean_text(str(candidate.get("kind") or "")).replace("\n", " ")
                    candidate["subject"] = clean_text(str(candidate.get("subject") or "")).replace("\n", " ")
                    candidate["posted"] = posted
                    candidate["due"] = clean_text(str(candidate.get("due") or "")).replace("\n", " ")
                    candidate["preview"] = preview
                    candidate["body_text"] = body_text
                    candidate["detail_href"] = detail_href
                    candidate["frame_index"] = frame_index
                    candidates.append(candidate)
        merged: list[dict[str, Any]] = []
        merged_by_base: dict[str, int] = {}

        def candidate_score(candidate: dict[str, Any]) -> int:
            return (
                (1200 if classroom_detail_href(candidate, getattr(page, "url", "") or CLASSROOM_URL) else 0)
                + (600 if str(candidate.get("data_stream_item_id") or "").strip() else 0)
                + (250 if bool(candidate.get("isolated", True)) else 0)
                + (200 if bool(candidate.get("is_structured_card")) else 0)
                + min(
                    max(
                        len(str(candidate.get("body_text") or "")),
                        len(str(candidate.get("preview") or "")),
                    ),
                    500,
                )
            )

        def merge_candidate(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
            if candidate_score(incoming) > candidate_score(existing):
                primary = dict(incoming)
                secondary = existing
            else:
                primary = dict(existing)
                secondary = incoming
            for key in (
                "detail_href",
                "href",
                "data_stream_item_id",
                "kind",
                "subject",
                "posted",
                "due",
                "title",
            ):
                if not primary.get(key) and secondary.get(key):
                    primary[key] = secondary.get(key)
            for key in ("body_text", "preview"):
                primary_text = str(primary.get(key) or "")
                secondary_text = str(secondary.get(key) or "")
                if len(secondary_text) > len(primary_text):
                    primary[key] = secondary_text
            links: list[dict[str, Any]] = []
            seen_link_keys: set[str] = set()
            for source in (primary.get("links"), secondary.get("links")):
                if not isinstance(source, list):
                    continue
                for link in source:
                    if not isinstance(link, dict):
                        continue
                    link_key = normalize(
                        "|".join(
                            [
                                str(link.get("href") or ""),
                                str(link.get("absolute_href") or ""),
                                str(link.get("label") or ""),
                            ]
                        )
                    )
                    if link_key in seen_link_keys:
                        continue
                    seen_link_keys.add(link_key)
                    links.append(link)
            if links:
                primary["links"] = links
            primary["detail_href"] = classroom_detail_href(primary, getattr(page, "url", "") or CLASSROOM_URL)
            primary["isolated"] = bool(primary.get("isolated", True) or secondary.get("isolated", True))
            primary["root_length"] = max(
                int(primary.get("root_length") or 0),
                int(secondary.get("root_length") or 0),
            )
            primary["root_title_count"] = max(
                int(primary.get("root_title_count") or 0),
                int(secondary.get("root_title_count") or 0),
            )
            primary["root_subject_count"] = max(
                int(primary.get("root_subject_count") or 0),
                int(secondary.get("root_subject_count") or 0),
            )
            return primary

        for candidate in candidates:
            base_key = normalize(
                "|".join(
                    [
                        str(candidate.get("title") or ""),
                        str(candidate.get("posted") or ""),
                    ]
                )
            ) or normalize(str(candidate.get("title") or ""))
            if not base_key:
                continue
            index = merged_by_base.get(base_key)
            if index is None:
                merged_by_base[base_key] = len(merged)
                merged.append(candidate)
            else:
                merged[index] = merge_candidate(merged[index], candidate)

        return merged

    def _open_classroom_post_by_title(self, page: Any, candidate: dict[str, Any]) -> bool:
        title = clean_text(str(candidate.get("title") or "")).replace("\n", " ")
        if not title:
            return False
        for frame in list(getattr(page, "frames", []) or []):
            try:
                result = frame.evaluate(CLASSROOM_OPEN_POST_BY_TITLE_SCRIPT, title)
            except Exception:
                continue
            if isinstance(result, dict) and result.get("clicked"):
                return True
        try:
            page.get_by_text(title, exact=True).first.click(timeout=1200)
            return True
        except Exception:
            return False

    def _extract_classroom_detail_by_title(
        self,
        page: Any,
        candidate: dict[str, Any],
    ) -> tuple[str, list[dict[str, str]]] | None:
        title = clean_text(str(candidate.get("title") or "")).replace("\n", " ")
        if not title:
            return None
        best_text = ""
        best_links: list[dict[str, str]] = []
        for frame in list(getattr(page, "frames", []) or []):
            try:
                result = frame.evaluate(CLASSROOM_DETAIL_BY_TITLE_SCRIPT, title)
            except Exception:
                continue
            if not isinstance(result, dict):
                continue
            text = visible_classroom_candidate_detail(
                {
                    **candidate,
                    "preview": result.get("body_text") or result.get("preview") or "",
                    "isolated": True,
                }
            )
            if len(text) > len(best_text):
                links = result.get("links", [])
                best_text = text
                best_links = [link for link in links if isinstance(link, dict)] if isinstance(links, list) else []
        if best_text:
            return best_text, best_links
        return None

    def _cached_classroom_record(self, candidate: dict[str, Any], platform: str) -> dict[str, Any] | None:
        if self._evidence_store is None:
            return None
        record_id = classroom_cache_record_id(candidate, platform)
        record = self._evidence_store.get("classroom_posts", record_id)
        if not record:
            return None
        if not classroom_cache_record_is_usable(record):
            return None
        cached_text = clean_text(str(record.get("raw_text") or ""))
        if not cached_text or len(cached_text) < 80:
            return None
        visible_detail = visible_classroom_candidate_detail(
            {
                **candidate,
                "preview": candidate.get("body_text") or candidate.get("preview") or "",
                "isolated": True,
            }
        )
        if visible_detail:
            visible_detail = clean_text(visible_detail)
        if (
            visible_detail
            and visible_detail not in cached_text
            and cached_text not in visible_detail
            and stable_hash(visible_detail) != record.get("content_hash")
        ):
            return None
        return record

    def _classroom_cached_detail_block(
        self,
        record: dict[str, Any],
        platform: str,
        index: int,
    ) -> list[str]:
        metadata = record.get("metadata") or {}
        return [
            "",
            f"POST REUTILIZADO DESDE CACHE {index}",
            f"Fuente: {platform}",
            "Metodo de captura: cache_historico",
            f"Asignatura/tema detectado: {metadata.get('topic') or metadata.get('subject') or 'No detectado'}",
            f"Titulo: {metadata.get('title') or 'No detectado'}",
            f"URL detalle detectada: {metadata.get('detail_href') or 'No detectada'}",
            f"Publicado/fecha visible en tarjeta: {metadata.get('posted') or 'No detectada'}",
            f"Estado cache: {record.get('status', 'unknown')}",
            "Texto historico cacheado:",
            clean_text(str(record.get("raw_text") or ""))[:4200],
        ]

    def _cache_classroom_detail(
        self,
        candidate: dict[str, Any],
        platform: str,
        detail_text: str,
        detail_href: str,
        capture_method: str,
    ) -> str:
        if self._evidence_store is None:
            return "ignored"
        detail_text = clean_text(detail_text)
        if not detail_text:
            return "ignored"
        title = clean_text(str(candidate.get("title") or "")).replace("\n", " ")
        topic = clean_text(str(candidate.get("subject") or "")).replace("\n", " ")
        posted = clean_text(str(candidate.get("posted") or "")).replace("\n", " ")
        metadata = {
            "source": platform,
            "topic": topic,
            "subject": topic,
            "title": title,
            "posted": posted,
            "published_sort": first_sortable_date(posted),
            "due": clean_text(str(candidate.get("due") or "")).replace("\n", " "),
            "detail_href": detail_href,
            "data_stream_item_id": str(candidate.get("data_stream_item_id") or ""),
            "capture_method": capture_method,
        }
        return self._evidence_store.upsert(
            "classroom_posts",
            classroom_cache_record_id({**candidate, "detail_href": detail_href}, platform),
            platform,
            detail_text,
            metadata,
            status=classroom_record_status("\n".join([title, posted, detail_text])),
        )

    def _wait_for_classroom_detail_open(self, page: Any, candidate: dict[str, Any]) -> bool:
        title = normalize(str(candidate.get("title") or ""))
        for _attempt in range(8):
            current_url = (getattr(page, "url", "") or "").lower()
            try:
                text, _stats = self._extract_page_text(page)
            except Exception:
                text = ""
            normalized_text = normalize(text)
            has_title = bool(title and title in normalized_text)
            has_detail_marker = looks_like_open_classroom_detail(text) or "/details" in current_url
            has_body = len(text) >= 250
            if (
                has_detail_marker
                and has_body
                and has_title
                and looks_like_complete_classroom_post_detail(text, str(candidate.get("title") or ""))
            ):
                return True
            try:
                page.wait_for_timeout(500)
            except Exception:
                time.sleep(0.5)
        return False

    def _extract_classroom_detail_from_current_page(
        self,
        page: Any,
        candidate: dict[str, Any],
    ) -> tuple[str, list[dict[str, str]]] | None:
        current_url = (getattr(page, "url", "") or "").lower()
        title = normalize(str(candidate.get("title") or ""))
        if "/details" not in current_url or not title:
            return None
        try:
            current_text, _stats = self._extract_page_text(page)
        except Exception:
            current_text = ""
        if title not in normalize(current_text):
            return None
        detail_text = self._extract_scrollable_classroom_detail_text(page)
        detail_text = compact_classroom_detail_text(detail_text, candidate)
        if not detail_text:
            return None
        detail_links = self._extract_visible_classroom_links(page)
        if (
            looks_like_complete_classroom_post_detail(detail_text, str(candidate.get("title") or ""))
            or classroom_text_has_detail_signal(detail_text)
            or (len(detail_text) >= 160 and title in normalize(detail_text))
        ):
            return detail_text, detail_links
        return None

    def _extract_scrollable_classroom_detail_text(self, page: Any) -> str:
        parts: list[str] = []
        for _index in range(6):
            try:
                text, _stats = self._extract_page_text(page)
            except Exception:
                text = ""
            if text:
                parts.append(text)
            moved = 0
            try:
                result = page.evaluate(CLASSROOM_SCROLL_DETAIL_SCRIPT)
                if isinstance(result, dict):
                    moved = int(result.get("moved", 0) or 0)
            except Exception:
                moved = 0
            if not moved:
                break
            try:
                page.wait_for_timeout(350)
            except Exception:
                time.sleep(0.35)
        return merge_text_parts(parts)

    def _scroll_classroom_listing(self, page: Any) -> bool:
        moved = 0
        try:
            result = page.evaluate(CLASSROOM_SCROLL_DETAIL_SCRIPT)
            if isinstance(result, dict):
                moved = int(result.get("moved", 0) or 0)
        except Exception:
            moved = 0
        try:
            page.wait_for_timeout(600)
        except Exception:
            time.sleep(0.6)
        return moved > 0

    def _extract_visible_classroom_links(self, page: Any) -> list[dict[str, str]]:
        links: list[dict[str, str]] = []
        seen: set[str] = set()
        script = """
() => Array.from(document.querySelectorAll('a[href]')).map((el) => {
  const style = window.getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  if (style.visibility === 'hidden' || style.display === 'none' || rect.width <= 0 || rect.height <= 0) return null;
  const label = [
    el.innerText,
    el.textContent,
    el.getAttribute('aria-label'),
    el.getAttribute('title'),
  ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
  return { label, href: el.getAttribute('href') || '' };
}).filter(Boolean).slice(0, 40)
"""
        for frame in list(getattr(page, "frames", []) or []):
            try:
                frame_links = frame.evaluate(script)
            except Exception:
                frame_links = []
            if not isinstance(frame_links, list):
                continue
            for link in frame_links:
                if not isinstance(link, dict):
                    continue
                label = clean_text(str(link.get("label") or "")).replace("\n", " ")[:220]
                href = str(link.get("href") or "").strip()
                if not label and not href:
                    continue
                absolute_href = urljoin(getattr(page, "url", "") or CLASSROOM_URL, href) if href else ""
                key = f"{label}|{href}|{absolute_href}"
                if key in seen:
                    continue
                seen.add(key)
                links.append({"label": label, "href": href, "absolute_href": absolute_href})
        return links[:20]

    def _return_from_classroom_detail(self, page: Any, original_url: str, section_terms: list[str]) -> None:
        current_url = getattr(page, "url", "") or ""
        if current_url and original_url and current_url != original_url:
            try:
                page.go_back(wait_until="domcontentloaded", timeout=7000)
                self._wait_after_interaction(page)
                return
            except Exception:
                pass
        try:
            page.keyboard.press("Escape")
            self._wait_after_interaction(page)
        except Exception:
            pass
        if section_terms and not self._page_has_any_text(page, section_terms):
            self._click_terms(page, section_terms)
            self._wait_after_interaction(page)

    def _extract_classroom_attachment_text(
        self,
        page: Any,
        allowed_hrefs: set[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        if self._context is None:
            return "", {"seen": 0, "pdf_attempts": 0, "pdf_texts": 0}

        candidates: list[dict[str, Any]] = []
        normalized_allowed_hrefs = set(allowed_hrefs) if allowed_hrefs is not None else None
        for frame in list(getattr(page, "frames", []) or []):
            try:
                frame_candidates = frame.evaluate(CLASSROOM_ATTACHMENT_LINKS_SCRIPT)
            except Exception:
                frame_candidates = []
            if isinstance(frame_candidates, list):
                for candidate in frame_candidates:
                    if isinstance(candidate, dict):
                        candidates.append(candidate)

        deduped: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for candidate in candidates:
            label = clean_text(str(candidate.get("label") or ""))
            href = str(candidate.get("href") or "").strip()
            absolute_href = urljoin(getattr(page, "url", "") or CLASSROOM_URL, href) if href else ""
            if normalized_allowed_hrefs is not None and href not in normalized_allowed_hrefs and absolute_href not in normalized_allowed_hrefs:
                continue
            key = f"{label}|{href}"
            if not label and not href:
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append({"label": label, "href": href, "is_pdf": bool(candidate.get("is_pdf"))})

        if not deduped:
            return "", {"seen": 0, "pdf_attempts": 0, "pdf_texts": 0}

        blocks: list[str] = [
            "ADJUNTOS VISIBLES CLASSROOM",
        ]
        for index, candidate in enumerate(deduped[:12], start=1):
            blocks.append(
                f"{index}. {candidate['label'] or '(sin etiqueta)'} | URL: {candidate['href'] or '(sin URL)'}"
            )

        pdf_candidates = [
            candidate
            for candidate in deduped
            if candidate.get("is_pdf")
            or ".pdf" in normalize(f"{candidate.get('label', '')} {candidate.get('href', '')}")
        ][:4]

        pdf_attempts = 0
        pdf_texts = 0
        for candidate in pdf_candidates:
            href = str(candidate.get("href") or "").strip()
            label = candidate.get("label") or "(PDF sin etiqueta)"
            if not href or href.startswith(("javascript:", "mailto:", "#")):
                continue
            pdf_attempts += 1
            target = None
            extracted_text = ""
            error_note = ""
            try:
                target = self._context.new_page()
                target.goto(urljoin(getattr(page, "url", "") or CLASSROOM_URL, href), wait_until="domcontentloaded", timeout=10000)
                try:
                    target.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    pass
                try:
                    target.wait_for_timeout(800)
                except Exception:
                    pass
                extracted_text, _ = self._extract_page_text(target)
                extracted_text = clean_text(extracted_text)[:15000]
            except Exception as exc:
                error_note = f"No pude abrir o leer el adjunto: {exc}"
            finally:
                if target is not None:
                    try:
                        target.close()
                    except Exception:
                        pass

            blocks.extend(
                [
                    "",
                    f"CONTENIDO EXTRAIDO DEL ADJUNTO PDF: {label}",
                    f"URL: {href}",
                ]
            )
            if extracted_text:
                pdf_texts += 1
                blocks.append(extracted_text)
            else:
                blocks.append(error_note or "No se pudo extraer texto visible del PDF con el visor del navegador.")

        return "\n".join(blocks).strip(), {
            "seen": len(deduped),
            "pdf_attempts": pdf_attempts,
            "pdf_texts": pdf_texts,
        }

    def _extract_schoolnet_grades_table(self, page: Any) -> tuple[str, dict[str, Any]]:
        parts: list[str] = []
        sources: list[dict[str, Any]] = []
        frames = list(getattr(page, "frames", []) or [])

        for index, frame in enumerate(frames):
            frame_label = "main" if index == 0 else f"iframe {index}"
            frame_url = getattr(frame, "url", "") or ""
            try:
                detail_text = frame.evaluate(SCHOOLNET_GRADES_DETAIL_SCRIPT)
                detail_text = clean_text(str(detail_text or ""))
            except Exception:
                detail_text = ""

            canonical_p1_text = schoolnet_canonical_p1_from_detail(detail_text)
            if canonical_p1_text:
                parts.append(canonical_p1_text)
                sources.append(
                    {
                        "source": frame_label,
                        "kind": "canonical_grades_p1",
                        "chars": len(canonical_p1_text),
                        "url": frame_url[:180],
                    }
                )
            else:
                try:
                    table_text = frame.evaluate(SCHOOLNET_GRADES_TABLE_SCRIPT)
                    table_text = clean_text(str(table_text or ""))
                except Exception:
                    table_text = ""

                if table_text:
                    parts.append(table_text)
                    sources.append(
                        {
                            "source": frame_label,
                            "kind": "structured_grades_p1_fallback",
                            "chars": len(table_text),
                            "url": frame_url[:180],
                        }
                    )

            if detail_text:
                parts.append(detail_text)
                sources.append(
                    {
                        "source": frame_label,
                        "kind": "structured_grades_detail",
                        "chars": len(detail_text),
                        "url": frame_url[:180],
                    }
                )

            try:
                subject_detail_text = frame.evaluate(SCHOOLNET_GRADES_BY_SUBJECT_SCRIPT)
                subject_detail_text = clean_text(str(subject_detail_text or ""))
            except Exception:
                subject_detail_text = ""

            if subject_detail_text:
                parts.append(subject_detail_text)
                sources.append(
                    {
                        "source": frame_label,
                        "kind": "structured_grades_by_subject",
                        "chars": len(subject_detail_text),
                        "url": frame_url[:180],
                    }
                )

        return "\n\n".join(dedupe(parts)), {"source_count": len(sources), "sources": sources[:8]}

    def _expand_schoolnet_grade_rows(self, page: Any) -> int:
        clicked = 0
        frames = list(getattr(page, "frames", []) or [])
        for frame in frames:
            try:
                result = frame.evaluate(SCHOOLNET_EXPAND_GRADE_ROWS_SCRIPT)
                if isinstance(result, dict):
                    clicked += int(result.get("clicked", 0) or 0)
            except Exception:
                continue
        return clicked

    def _expand_schoolnet_conducta_rows(self, page: Any) -> int:
        clicked = 0
        frames = list(getattr(page, "frames", []) or [])
        for frame in frames:
            try:
                result = frame.evaluate(SCHOOLNET_EXPAND_CONDUCT_ROWS_SCRIPT)
                if isinstance(result, dict):
                    clicked += int(result.get("clicked", 0) or 0)
            except Exception:
                continue
        return clicked

    def _expand_classroom_visible_items(self, page: Any) -> int:
        clicked = 0
        frames = list(getattr(page, "frames", []) or [])
        for frame in frames:
            try:
                result = frame.evaluate(CLASSROOM_EXPAND_VISIBLE_ITEMS_SCRIPT)
                if isinstance(result, dict):
                    clicked += int(result.get("clicked", 0) or 0)
            except Exception:
                continue
        return clicked

    def _click_schoolnet_conducta_category(self, page: Any, terms: list[str]) -> dict[str, Any] | None:
        frames = list(getattr(page, "frames", []) or [])
        for frame in frames:
            try:
                result = frame.evaluate(SCHOOLNET_CLICK_CONDUCTA_CATEGORY_SCRIPT, terms)
            except Exception:
                continue
            if isinstance(result, dict) and result.get("clicked"):
                return result
        return None

    def _extract_schoolnet_conducta_views(self, page: Any) -> tuple[str, dict[str, Any]]:
        categories = [
            ("Anotaciones Positivas", ["anotaciones positivas", "positivas", "positiva"]),
            ("Anotaciones Negativas", ["anotaciones negativas", "negativas", "negativa"]),
            ("Anotaciones Neutras", ["anotaciones neutras", "neutras", "neutra"]),
        ]
        blocks: list[str] = []
        notes: list[str] = []
        seen_hashes: set[str] = set()
        clicks = 0
        failures = 0
        sources = 0
        views_captured = 0

        for index, (label, terms) in enumerate(categories):
            click_result = self._click_schoolnet_conducta_category(page, terms)
            clicked = bool(click_result)
            if clicked:
                clicks += 1
                self._wait_after_interaction(page)
            elif index > 0:
                failures += 1
                notes.append(f"No pude abrir la vista de conducta: {label}.")
                continue

            expanded_rows = self._expand_schoolnet_conducta_rows(page)
            if expanded_rows:
                self._wait_after_interaction(page)

            detail_text, detail_stats = self._extract_schoolnet_conducta_detail(page)
            try:
                raw_text, raw_stats = self._extract_page_text(page)
            except Exception:
                raw_text = ""
                raw_stats = {"source_count": 0}
            view_text = detail_text or raw_text
            signature = stable_hash(view_text)
            if not view_text or signature in seen_hashes:
                if label == "Anotaciones Negativas":
                    notes.append("La vista de Anotaciones Negativas no entrego filas distintas a la vista anterior.")
                continue
            seen_hashes.add(signature)
            views_captured += 1
            sources += int(detail_stats.get("source_count", 0) or raw_stats.get("source_count", 0) or 1)
            blocks.extend(
                [
                    "",
                    f"VISTA SCHOOLNET CONDUCTA: {label}",
                    f"Click categoria: {'si' if clicked else 'no; se leyo la vista actual'}",
                    f"Control detectado: {clean_text(str((click_result or {}).get('text') or 'No detectado'))}",
                    f"Filas expandidas en esta vista: {expanded_rows}",
                    view_text,
                ]
            )

        return "\n".join(blocks).strip(), {
            "source_count": sources,
            "conducta_view_clicks": clicks,
            "conducta_view_failures": failures,
            "views_captured": views_captured,
            "notes": notes,
        }

    def _extract_schoolnet_conducta_detail(self, page: Any) -> tuple[str, dict[str, Any]]:
        parts: list[str] = []
        sources: list[dict[str, Any]] = []
        frames = list(getattr(page, "frames", []) or [])

        for index, frame in enumerate(frames):
            frame_label = "main" if index == 0 else f"iframe {index}"
            frame_url = getattr(frame, "url", "") or ""
            try:
                detail_text = frame.evaluate(SCHOOLNET_CONDUCTA_DETAIL_SCRIPT)
                detail_text = clean_text(str(detail_text or ""))
            except Exception:
                detail_text = ""

            if detail_text:
                parts.append(detail_text)
                sources.append(
                    {
                        "source": frame_label,
                        "kind": "structured_conducta_detail",
                        "chars": len(detail_text),
                        "url": frame_url[:180],
                    }
                )

        return "\n\n".join(dedupe(parts)), {"source_count": len(sources), "sources": sources[:8]}


def classify_platform(url: str) -> str | None:
    lower = url.lower()
    if "schoolnet" in lower or "colegium" in lower:
        return "SchoolNet"
    if "classroom.google" in lower or "classroom.google.com" in lower:
        return "Google Classroom"
    return None


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    previous = None
    for raw in text.split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        if line == previous:
            continue
        previous = line
        lines.append(line)
    return "\n".join(lines)


def merge_text_parts(parts: list[str]) -> str:
    output: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for line in clean_text(part).splitlines():
            key = normalize(line)
            if key and key not in seen:
                seen.add(key)
                output.append(line)
    return "\n".join(output)


def login_status_note(platform: str, url: str, text: str) -> str | None:
    lower_url = url.lower()
    lower_text = text.lower()
    if platform.startswith("SchoolNet"):
        if "/login" in lower_url or all(word in lower_text for word in ("usuario", "clave")):
            return "SchoolNet parece estar en pantalla de login. Inicia sesion y vuelve a generar."
    if platform.startswith("Google Classroom"):
        if "accounts.google.com" in lower_url:
            return "Google Classroom redirigio a login de Google. Inicia sesion y vuelve a generar."
        if "sign in" in lower_text or "iniciar sesion" in lower_text or "iniciar sesión" in lower_text:
            return "Google Classroom parece requerir inicio de sesion."
    return None


KEYWORDS = {
    "pendientes": [
        "tarea",
        "assignment",
        "entrega",
        "entregar",
        "due",
        "plazo",
        "pendiente",
        "missing",
        "atrasad",
        "trabajo",
        "actividad",
    ],
    "evaluaciones_notas": [
        "nota",
        "calificacion",
        "calificación",
        "grade",
        "score",
        "puntos",
        "points",
        "prueba",
        "evaluacion",
        "evaluación",
        "control",
        "quiz",
        "examen",
    ],
    "anotaciones": [
        "anotacion",
        "anotación",
        "observacion",
        "observación",
        "positiva",
        "negativa",
        "conducta",
        "convivencia",
        "disciplina",
        "felicit",
    ],
    "contenido": [
        "material",
        "contenido",
        "clase",
        "unidad",
        "tema",
        "lectura",
        "recurso",
        "documento",
        "video",
        "guia",
        "guía",
        "posted",
        "publicado",
    ],
}

GRADE_RE = re.compile(r"\b(?:[1-7](?:[,.]\d)?|[0-9]{1,3}%|promedio|final)\b", re.IGNORECASE)
DATE_RE = re.compile(
    r"(\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b|\b\d{1,2}\s+(?:de\s+)?[a-z\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1]{3,}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\s+\d{1,2}\b|\b(?:hoy|manana|ma\u00f1ana|ayer)\b)",
    re.IGNORECASE,
)
CONDUCTA_KEYWORDS = KEYWORDS["anotaciones"] + ["atraso", "asistencia", "registro", "incidente"]
CALIFICACIONES_KEYWORDS = KEYWORDS["evaluaciones_notas"] + ["promedio", "ponderacion", "ponderación", "asignatura"]
CLASSROOM_WORK_KEYWORDS = KEYWORDS["pendientes"] + KEYWORDS["evaluaciones_notas"] + [
    "4-a",
    "trabajo de clase",
    "classwork",
    "sin entregar",
    "entregado",
    "fecha limite",
]
CLASSROOM_STUDY_KEYWORDS = [
    "prueba",
    "evaluacion",
    "evaluación",
    "control",
    "tarea",
    "entrega",
]
CLASSROOM_STUDY_KEYWORDS.extend(
    [
        "temario",
        "guia",
        "guA-a",
        "material",
        "repasar",
        "test",
        "practice",
        "practica",
        "study",
        "vocabulary",
        "vocabulario",
    ]
)
CLASSROOM_STREAM_POST_KEYWORDS = [
    "prueba",
    "evaluacion",
    "evaluaciA3n",
    "control",
    "quiz",
    "examen",
    "tarea",
    "entrega",
    "trabajo",
    "actividad",
    "temario",
    "contenido",
    "materia",
    "repasar",
    "estudiar",
    "lectura",
    "dictado",
    "guia",
    "guA-a",
    "material",
    "hoja de ruta",
    "fecha limite",
]
CLASSROOM_STREAM_POST_KEYWORDS.extend(
    [
        "test",
        "practice",
        "practica",
        "exam",
        "homework",
        "worksheet",
        "study",
        "vocabulary",
        "vocabulario",
        "wordwall",
    ]
)
CLASSROOM_POST_TITLE_KEYWORDS = [
    "prueba",
    "evaluacion",
    "evaluaciA3n",
    "control",
    "quiz",
    "examen",
    "tarea",
    "entrega",
    "trabajo",
    "test",
    "practice",
    "practica",
    "exam",
    "homework",
    "worksheet",
    "dictado",
    "temario",
    "guia de estudio",
    "guA-a de estudio",
    "hoja de ruta",
]
SUBJECT_TERMS = [
    "matematica",
    "matemática",
    "matematicas",
    "matemáticas",
    "lenguaje",
    "comunicacion",
    "comunicación",
    "historia",
    "ciencias",
    "ingles",
    "inglés",
    "religion",
    "religión",
    "arte",
    "artes",
    "musica",
    "música",
    "tecnologia",
    "tecnología",
    "educacion fisica",
    "educación física",
    "orientacion",
    "orientación",
]
THEME_KEYWORDS = ["temario", "tema", "unidad", "contenido", "objetivo", "materia", "repasar"]

SPANISH_MONTHS = {
    "enero": 1,
    "ene": 1,
    "febrero": 2,
    "feb": 2,
    "marzo": 3,
    "mar": 3,
    "abril": 4,
    "abr": 4,
    "mayo": 5,
    "may": 5,
    "junio": 6,
    "jun": 6,
    "julio": 7,
    "jul": 7,
    "agosto": 8,
    "ago": 8,
    "septiembre": 9,
    "setiembre": 9,
    "sep": 9,
    "sept": 9,
    "set": 9,
    "octubre": 10,
    "oct": 10,
    "noviembre": 11,
    "nov": 11,
    "diciembre": 12,
    "dic": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "apr": 4,
    "june": 6,
    "july": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "dec": 12,
}


def extract_date_mentions(text: str, base_date: dt.date | None = None) -> list[dict[str, Any]]:
    base_date = base_date or current_report_date()
    mentions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in DATE_RE.finditer(text):
        raw = match.group(0).strip()
        parsed, kind = parse_date_mention(raw, base_date)
        if kind == "unknown" and parsed is None:
            continue
        key = f"{normalize(raw)}|{parsed.isoformat() if parsed else ''}|{kind}"
        if key in seen:
            continue
        seen.add(key)
        mentions.append({"raw": raw, "date": parsed, "kind": kind})
    return mentions


def parse_date_mention(raw: str, base_date: dt.date) -> tuple[dt.date | None, str]:
    normalized = normalize(raw).strip(" .,:;()[]")
    if normalized == "hoy":
        return base_date, "relative"
    if normalized == "ayer":
        return base_date - dt.timedelta(days=1), "relative"
    if normalized == "manana":
        return base_date + dt.timedelta(days=1), "relative"

    numeric_match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", normalized)
    if numeric_match:
        day = int(numeric_match.group(1))
        month = int(numeric_match.group(2))
        year_text = numeric_match.group(3)
        year = base_date.year
        if year_text:
            year = int(year_text)
            if year < 100:
                year += 2000
        try:
            return dt.date(year, month, day), "concrete"
        except ValueError:
            return None, "unknown"

    text_match = re.fullmatch(r"(\d{1,2})\s+(?:de\s+)?([a-z]+)", normalized)
    if text_match:
        day = int(text_match.group(1))
        month = SPANISH_MONTHS.get(text_match.group(2))
        if month:
            try:
                return dt.date(base_date.year, month, day), "concrete"
            except ValueError:
                return None, "unknown"

    month_first_match = re.fullmatch(r"([a-z]+)\s+(\d{1,2})", normalized)
    if month_first_match:
        month = SPANISH_MONTHS.get(month_first_match.group(1))
        day = int(month_first_match.group(2))
        if month:
            try:
                return dt.date(base_date.year, month, day), "concrete"
            except ValueError:
                return None, "unknown"

    return None, "unknown"


def should_skip_past_classroom_item(date_mentions: list[dict[str, Any]], today: dt.date) -> bool:
    concrete_dates = [
        mention["date"]
        for mention in date_mentions
        if mention.get("kind") == "concrete" and isinstance(mention.get("date"), dt.date)
    ]
    has_recent_relative_marker = any(
        mention.get("kind") == "relative"
        and isinstance(mention.get("date"), dt.date)
        and mention["date"] >= today - dt.timedelta(days=1)
        for mention in date_mentions
    )
    return bool(concrete_dates) and max(concrete_dates) < today and not has_recent_relative_marker


def format_date_mentions(date_mentions: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for mention in date_mentions:
        raw = str(mention.get("raw") or "")
        parsed = mention.get("date")
        if isinstance(parsed, dt.date):
            parts.append(f"{raw} -> {parsed.isoformat()}")
        elif raw:
            parts.append(raw)
    return "; ".join(dedupe(parts))


def has_any_normalized_keyword(text: str, keywords: list[str]) -> bool:
    normalized_text = normalize(text)
    return any(normalize(keyword) in normalized_text for keyword in keywords)


def matched_keywords(text: str, keywords: list[str]) -> list[str]:
    normalized_text = normalize(text)
    matches: list[str] = []
    for keyword in keywords:
        normalized_keyword = normalize(keyword)
        if normalized_keyword and normalized_keyword in normalized_text:
            matches.append(keyword)
    return dedupe(matches)[:8]


def latest_date(date_mentions: list[dict[str, Any]]) -> dt.date | None:
    dates = [
        mention["date"]
        for mention in date_mentions
        if isinstance(mention.get("date"), dt.date)
    ]
    return max(dates) if dates else None


def normalize_classroom_detail_url(href: str, base_url: str) -> str:
    href = str(href or "").strip()
    if not href or href.startswith(("javascript:", "mailto:", "#")):
        return ""
    absolute = urljoin(base_url or CLASSROOM_URL, href)
    parsed = urlparse(absolute)
    if "classroom.google." not in parsed.netloc:
        return ""
    path = parsed.path.rstrip("/")
    if not (
        path.startswith(f"/c/{CLASSROOM_4A_COURSE_ID}/")
        or path.startswith(f"/w/{CLASSROOM_4A_COURSE_ID}/")
    ):
        return ""
    if "/details" in path:
        return urlunparse(parsed._replace(path=path, fragment=""))
    if re.search(r"/(?:m|a)/[^/]+$", path):
        return urlunparse(parsed._replace(path=f"{path}/details", query="", fragment=""))
    return ""


def normalize_classroom_topic_url(href: str, base_url: str) -> str:
    href = str(href or "").strip()
    if not href or href.startswith(("javascript:", "mailto:", "#")):
        return ""
    absolute = urljoin(base_url or CLASSROOM_URL, href)
    parsed = urlparse(absolute)
    if "classroom.google." not in parsed.netloc:
        return ""
    path = parsed.path.rstrip("/")
    expected_prefix = f"/w/{CLASSROOM_4A_COURSE_ID}/tc/"
    if not path.startswith(expected_prefix):
        return ""
    return urlunparse(parsed._replace(path=path, query="", fragment=""))


def classroom_detail_href(candidate: dict[str, Any], base_url: str) -> str:
    raw_hrefs: list[str] = []
    for key in ("detail_href", "href"):
        value = str(candidate.get(key) or "").strip()
        if value:
            raw_hrefs.append(value)
    links = candidate.get("links", [])
    if isinstance(links, list):
        for link in links:
            if not isinstance(link, dict):
                continue
            href = str(link.get("href") or "").strip()
            if href:
                raw_hrefs.append(href)
    for href in raw_hrefs:
        detail_url = normalize_classroom_detail_url(href, base_url)
        if detail_url:
            return detail_url
    return ""


def classroom_candidate_skip_reason(candidate: dict[str, Any], today: dt.date) -> str | None:
    title = str(candidate.get("title") or "")
    posted = str(candidate.get("posted") or "")
    preview = str(candidate.get("preview") or "")
    if is_generic_classroom_title(title):
        return "not_relevant"
    if not has_any_normalized_keyword(title, CLASSROOM_POST_TITLE_KEYWORDS):
        return "not_relevant"

    posted_date = latest_date(extract_date_mentions(posted, today))
    if posted_date and posted_date < today - dt.timedelta(days=MAX_CLASSROOM_POST_AGE_DAYS):
        return "old"

    content_dates = extract_date_mentions("\n".join([title, preview]), today)
    if should_skip_past_classroom_item(content_dates, today):
        return "past"
    return None


def looks_like_global_classroom_listing_text(text: str) -> bool:
    cleaned = clean_text(str(text or ""))
    if not cleaned:
        return False
    normalized_text = normalize(cleaned)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    first_lines = normalize(" ".join(lines[:8]))
    if (
        "filtro por tema" in first_lines
        or "todos los temas" in first_lines
        or "ocultar todo" in first_lines
        or "trabajo de clase" in first_lines
    ):
        return True
    relevant_titles = {
        normalize(line)
        for line in lines
        if len(line) <= 220 and has_any_normalized_keyword(line, CLASSROOM_POST_TITLE_KEYWORDS)
    }
    subjects = {
        normalize(subject)
        for subject in SUBJECT_TERMS
        if has_normalized_term(cleaned, subject)
    }
    if len(cleaned) > 900 and len(relevant_titles) > 2:
        return True
    if len(cleaned) > 1200 and len(subjects) > 1:
        return True
    return False


def looks_like_isolated_classroom_post_preview(text: str, title: str = "") -> bool:
    cleaned = clean_text(str(text or ""))
    if len(cleaned) < 80:
        return False
    if looks_like_global_classroom_listing_text(cleaned):
        return False
    title_norm = normalize(title)
    relevant_titles = {
        normalize(line)
        for line in cleaned.splitlines()
        if len(line.strip()) <= 220 and has_any_normalized_keyword(line, CLASSROOM_POST_TITLE_KEYWORDS)
    }
    if title_norm and title_norm in relevant_titles:
        relevant_titles.discard(title_norm)
    return len(relevant_titles) <= 1


def looks_like_complete_classroom_post_detail(text: str, title: str = "") -> bool:
    cleaned = clean_text(str(text or ""))
    if len(cleaned) < 180:
        return False
    if looks_like_global_classroom_listing_text(cleaned):
        return False
    normalized_text = normalize(cleaned)
    title_norm = normalize(title)
    has_title = not title_norm or title_norm in normalized_text
    has_marker = any(
        marker in normalized_text
        for marker in (
            "comentarios de la clase",
            "class comments",
            "anade un comentario",
            "add class comment",
            "publicado",
            "posted",
            "material",
            "tarea",
        )
    )
    return has_title and has_marker


def visible_classroom_candidate_detail(candidate: dict[str, Any]) -> str:
    preview = clean_text(str(candidate.get("preview") or ""))
    if not preview:
        return ""
    if not bool(candidate.get("isolated", True)):
        return ""
    useful_lines: list[str] = []
    for line in preview.splitlines():
        normalized_line = normalize(line)
        if not normalized_line:
            continue
        if normalized_line in (
            "ver material",
            "material",
            "book",
            "comentarios de la clase",
            "opciones",
            "class comments",
        ):
            continue
        useful_lines.append(line)
    detail = "\n".join(useful_lines).strip()
    if len(detail) < 120 and len(useful_lines) < 3:
        return ""
    if not looks_like_isolated_classroom_post_preview(detail, str(candidate.get("title") or "")):
        return ""
    return detail


def clean_classroom_topic_label(label: str) -> str:
    lines = [
        clean_text(line).strip()
        for line in str(label or "").splitlines()
        if clean_text(line).strip()
    ]
    if not lines:
        lines = [clean_text(str(label or "")).strip()]
    compacted: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized_line = normalize(line)
        if normalized_line and normalized_line not in seen:
            seen.add(normalized_line)
            compacted.append(line)
    if len(compacted) == 1:
        return compacted[0]
    subject_lines = [line for line in compacted if is_relevant_classroom_topic_label(line)]
    return subject_lines[0] if subject_lines else " ".join(compacted)


def is_relevant_classroom_topic_label(label: str) -> bool:
    normalized_label = normalize(label).strip()
    if not normalized_label:
        return False
    if normalized_label in CLASSROOM_TOPIC_EXCLUDED_LABELS:
        return False
    if any(excluded in normalized_label for excluded in CLASSROOM_TOPIC_EXCLUDED_LABELS):
        return False
    return any(term in normalized_label for term in CLASSROOM_TOPIC_LABEL_TERMS)


def is_generic_classroom_title(title: str) -> bool:
    normalized_title = normalize(title)
    if not normalized_title:
        return True
    generic_titles = {
        "tareas pendientes",
        "pending assignments",
        "sin tareas pendientes",
        "trabajo de clase",
        "ver todo",
        "tu trabajo",
        "ver tu trabajo",
        "comentarios de la clase",
        "class comments",
        "material",
        "book",
        "opciones",
    }
    return (
        normalized_title in generic_titles
        or normalized_title.startswith("tareas pendientes ")
        or normalized_title.startswith("trabajo de clase ")
    )


def looks_like_open_classroom_detail(text: str) -> bool:
    normalized_text = normalize(text)
    return (
        "comentarios de la clase" in normalized_text
        or "class comments" in normalized_text
        or "anade un comentario" in normalized_text
        or "add class comment" in normalized_text
    )


def detect_relevant_classroom_title(text: str) -> str:
    for line in clean_text(text).splitlines()[:40]:
        candidate = line.strip()
        if len(candidate) > 220:
            continue
        if is_generic_classroom_title(candidate):
            continue
        if has_any_normalized_keyword(candidate, CLASSROOM_POST_TITLE_KEYWORDS):
            return candidate
    return ""


def extract_classroom_posted_line(text: str) -> str:
    for line in clean_text(text).splitlines()[:50]:
        if has_any_normalized_keyword(line, CLASSROOM_POST_TITLE_KEYWORDS):
            continue
        normalized_line = normalize(line)
        if (
            "publicado" in normalized_line
            or "posted" in normalized_line
            or normalized_line in ("ayer", "hoy")
            or DATE_RE.search(line)
        ):
            return line[:180]
    return ""


def compact_classroom_detail_text(text: str, candidate: dict[str, Any]) -> str:
    title = normalize(str(candidate.get("title") or ""))
    posted = normalize(str(candidate.get("posted") or ""))
    due = normalize(str(candidate.get("due") or ""))
    output: list[str] = []
    seen: set[str] = set()
    started = not title
    for line in clean_text(text).splitlines():
        normalized_line = normalize(line)
        if not normalized_line or normalized_line in seen:
            continue
        seen.add(normalized_line)
        if (
            "comentarios de la clase" in normalized_line
            or "class comments" in normalized_line
            or "anade un comentario" in normalized_line
            or "add class comment" in normalized_line
            or "comentario privado" in normalized_line
            or "private comment" in normalized_line
        ):
            break
        if normalized_line in ("classroom", "trabajo de clase", "tablon", "stream", "personas", "calificaciones"):
            continue
        if not started:
            if (
                title
                and title in normalized_line
                and not normalized_line.startswith("opciones de ")
                and not normalized_line.startswith("archivo adjunto")
                and len(line) <= len(str(candidate.get("title") or "")) + 120
            ):
                started = True
            else:
                continue
        if title and normalized_line == title:
            output.append(line)
            continue
        if posted and normalized_line == posted:
            output.append(line)
            continue
        if due and normalized_line == due:
            output.append(line)
            continue
        if normalized_line in (
            "material",
            "book",
            "assignment",
            "tarea",
            "more_vert",
            "mas opciones",
            "ver material",
            "view material",
        ):
            continue
        if normalized_line.startswith("opciones de "):
            continue
        output.append(line)
        if len("\n".join(output)) >= 4200:
            break
    return "\n".join(output).strip()


def interesting_lines(text: str, keywords: list[str], limit: int = 12) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        lower = normalize(line)
        if any(normalize(keyword) in lower for keyword in keywords) or DATE_RE.search(line):
            clipped = line[:260].strip()
            key = clipped.lower()
            if clipped and key not in seen:
                seen.add(key)
                results.append(clipped)
        if len(results) >= limit:
            break
    return results


def focused_lines(
    snapshots: list[PageSnapshot],
    platform_fragment: str,
    keywords: list[str],
    limit: int = 18,
    include_grades: bool = False,
) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    target = normalize(platform_fragment)
    normalized_keywords = [normalize(keyword) for keyword in keywords]
    for snapshot in snapshots:
        if target not in normalize(snapshot.platform):
            continue
        for line in snapshot.text.splitlines():
            normalized_line = normalize(line)
            has_keyword = any(keyword in normalized_line for keyword in normalized_keywords)
            has_date = DATE_RE.search(line) is not None
            has_grade = include_grades and GRADE_RE.search(line) is not None
            if not (has_keyword or has_date or has_grade):
                continue
            clipped = line[:260].strip()
            key = normalize(clipped)
            if clipped and key not in seen:
                seen.add(key)
                results.append(clipped)
            if len(results) >= limit:
                return results
    return results


def parse_classroom_study_items(text: str) -> list[dict[str, str]]:
    lines = clean_text(text).splitlines()
    normalized_keywords = [normalize(keyword) for keyword in CLASSROOM_STUDY_KEYWORDS]
    blocks: list[tuple[str, str]] = []
    seen_blocks: set[str] = set()
    trigger_indices = [
        index
        for index, line in enumerate(lines)
        if any(keyword in normalize(line) for keyword in normalized_keywords)
    ]
    trigger_set = set(trigger_indices)

    for index, line in enumerate(lines):
        if index not in trigger_set:
            continue
        previous_trigger = max((item for item in trigger_indices if item < index), default=-1)
        next_trigger = min((item for item in trigger_indices if item > index), default=len(lines))
        start = max(previous_trigger + 1, index - 1)
        end = min(next_trigger, index + 5)
        block = "\n".join(lines[start:end]).strip()
        key = normalize(block)
        if block and key not in seen_blocks:
            seen_blocks.add(key)
            blocks.append((block, line))

    items: list[dict[str, str]] = []
    for block, trigger_line in blocks:
        detail_source = block if relevant_line_count(block) == 1 else trigger_line
        items.append(
            {
                "type": detect_study_item_type(trigger_line),
                "date": extract_first_date(detail_source),
                "subject": extract_subject(block),
                "theme": extract_theme(detail_source),
                "text": block,
            }
        )
    return items


def detect_study_item_type(text: str) -> str:
    normalized_text = normalize(text)
    if any(term in normalized_text for term in ("prueba", "evaluacion", "control")):
        return "Prueba/evaluacion"
    if any(term in normalized_text for term in ("tarea", "entrega", "assignment")):
        return "Tarea/entrega"
    return "Actividad"


def relevant_line_count(text: str) -> int:
    normalized_keywords = [normalize(keyword) for keyword in CLASSROOM_STUDY_KEYWORDS]
    count = 0
    for line in clean_text(text).splitlines():
        normalized_line = normalize(line)
        if any(keyword in normalized_line for keyword in normalized_keywords):
            count += 1
    return count


def extract_first_date(text: str) -> str:
    match = DATE_RE.search(text)
    return match.group(0) if match else "No detectada"


def extract_subject(text: str) -> str:
    for subject in SUBJECT_TERMS:
        if has_normalized_term(text, subject):
            return subject.capitalize()
    return "No detectada"


def extract_theme(text: str) -> str:
    lines = clean_text(text).splitlines()
    for line in lines:
        if any(has_normalized_term(line, keyword) for keyword in THEME_KEYWORDS):
            return line[:220]
    return "No detectado"


def has_normalized_term(text: str, term: str) -> bool:
    normalized_text = normalize(text)
    normalized_term = normalize(term)
    pattern = r"(?<![a-z0-9])" + re.escape(normalized_term).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
    return re.search(pattern, normalized_text) is not None


def build_study_prompt(classroom_snapshots: list[PageSnapshot]) -> str:
    items: list[dict[str, str]] = []
    original_blocks: list[str] = []
    alerts: list[str] = []

    for snapshot in classroom_snapshots:
        if snapshot.status != "ok":
            alerts.extend(f"{snapshot.platform}: {note}" for note in snapshot.notes)
        parsed_items = parse_classroom_study_items(snapshot.text)
        for item in parsed_items:
            item["source"] = snapshot.platform
            items.append(item)
            original_blocks.append(f"[{snapshot.platform}]\n{item['text']}")

    items = dedupe_study_items(items)
    original_blocks = dedupe(original_blocks)

    detected_info = format_study_items(items)
    original_texts = "\n\n".join(original_blocks) if original_blocks else "No se detectaron textos visibles con prueba, evaluacion, control, tarea o entrega."
    alert_text = bulletize(dedupe(alerts), "Sin alertas de lectura de Classroom.")

    prompt = [
        "Actua como tutor escolar. Necesito que generes un plan concreto de estudio para un estudiante de 4A.",
        "",
        "Usa SOLO la informacion extraida desde Google Classroom que copio abajo. Si falta informacion, indicalo como supuesto o pregunta pendiente. No inventes fechas, asignaturas ni contenidos.",
        "",
        "IMPORTANTE: La lectura de Classroom se hace solo desde Trabajo de clase, recorriendo Filtro por tema/asignatura. Si aparece un bloque POSTS RELEVANTES VISIBLES EN GOOGLE CLASSROOM, usalo como fuente principal para texto publicado por docentes.",
        "",
        "OBJETIVO",
        "Crear un plan de estudio practico, con tareas por dia, prioridades, repasos, ejercicios sugeridos y checklist final.",
        "",
        "INFORMACION DETECTADA EN CLASSROOM 4A",
        detected_info,
        "",
        "TEXTOS ORIGINALES RELEVANTES",
        original_texts,
        "",
        "ALERTAS DE LECTURA",
        alert_text,
        "",
        "FORMATO DE RESPUESTA ESPERADO",
        "1. Resumen de evaluaciones y tareas proximas.",
        "2. Plan de estudio por dia.",
        "3. Temas que debe repasar.",
        "4. Ejercicios o actividades sugeridas.",
        "5. Checklist para antes de la prueba o entrega.",
        "6. Preguntas que debo resolver si la informacion esta incompleta.",
    ]
    return "\n".join(prompt).strip() + "\n"


def format_study_items(items: list[dict[str, str]]) -> str:
    if not items:
        return "- No se detectaron pruebas, evaluaciones, controles, tareas o entregas en el texto visible de Classroom 4A."
    blocks: list[str] = []
    for item in items:
        blocks.append(
            "\n".join(
                [
                    f"- Tipo: {item.get('type', 'Actividad')}",
                    f"  Fecha: {item.get('date', 'No detectada')}",
                    f"  Asignatura: {item.get('subject', 'No detectada')}",
                    f"  Tema/temario: {item.get('theme', 'No detectado')}",
                    f"  Fuente: {item.get('source', 'Classroom 4A')}",
                    f"  Texto: \"{single_line(item.get('text', ''))}\"",
                ]
            )
        )
    return "\n".join(blocks)


def dedupe_study_items(items: list[dict[str, str]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        key = normalize(item.get("text", ""))
        if key and key not in seen:
            seen.add(key)
            output.append(item)
    return output


def single_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:900]


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text or "")
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn").lower()


def bulletize(lines: list[str], empty_message: str) -> str:
    if not lines:
        return f"- {empty_message}"
    return "\n".join(f"- {line}" for line in lines)


def build_master_chatgpt_prompt(snapshots: list[PageSnapshot], manual_notes: str = "") -> str:
    today = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    reference_date = current_report_date()
    report_date = reference_date.isoformat()
    cutoff_date = reference_date.isoformat()
    cutoff_year = reference_date.year
    evidence_blocks: list[str] = []

    for snapshot in snapshots:
        evidence_blocks.append(format_raw_evidence_block(snapshot))

    if manual_notes.strip():
        evidence_blocks.append(
            "\n".join(
                [
                    "----- TEXTO MANUAL OPCIONAL -----",
                    "Fuente: Texto pegado manualmente en la app",
                    "Estado: manual",
                    "",
                    "TEXTO VISIBLE / EVIDENCIA",
                    clean_text(manual_notes),
                    "----- FIN TEXTO MANUAL OPCIONAL -----",
                ]
            )
        )

    if not evidence_blocks:
        evidence_blocks.append("No habia pestanas de SchoolNet/Classroom disponibles al generar este prompt.")

    prompt = [
        f"Actua como asistente escolar y disenador de infografias educativas. El estudiante se llama {STUDENT_NAME}. Usa SOLO la evidencia copiada abajo.",
        "",
        "No inventes datos. Si falta informacion, escribe exactamente \"No detectado\". Usa PENDIENTES / DUDAS solo para listar informacion incompleta o contradicciones, pero no rellenes datos ausentes.",
        "La fuente principal es la EVIDENCIA CRUDA copiada al final del prompt. Prioriza esa evidencia por sobre inferencias, expectativas escolares o conocimiento general.",
        "",
        "IMPORTANTE SOBRE ADJUNTOS: usa el contenido de adjuntos solo si aparece textual en la evidencia cruda. Si solo aparece el nombre de un PDF/archivo, no inventes su contenido: marcalo como adjunto visible pendiente de lectura. No concluyas que no existe material; indica que falta leer el contenido del adjunto.",
        "",
        "IMPORTANTE SOBRE CLASSROOM: la evidencia de Classroom viene solo de Trabajo de clase, recorriendo Filtro por tema/asignatura. Cada snapshot de tema puede contener texto crudo visible y posts relevantes abiertos o capturados bajo titulos como prueba, evaluacion, tarea, control, test, guia, temario o material.",
        "Si existe un bloque DETALLE DE POSTS RELEVANTES ABIERTOS EN GOOGLE CLASSROOM o POSTS RELEVANTES VISIBLES EN GOOGLE CLASSROOM, dale prioridad sobre el texto crudo de la vista por tema. Ese bloque contiene items abiertos o ya desplegados en Trabajo de clase por tema/asignatura.",
        "Dentro de esos posts o vistas por tema, conserva el sentido del texto publicado: fecha de prueba/evaluacion/tarea, autor si aparece, temario, contenidos a estudiar, instrucciones y adjuntos/links relacionados.",
        "",
        f"FECHA DE CORTE PARA TAREAS Y EVALUACIONES: {cutoff_date}.",
        "Incluye en TAREAS y EVALUACIONES solo actividades con fecha de hoy o futura. Excluye toda tarea, entrega, prueba, control o evaluacion con fecha anterior a la fecha de corte.",
        "Las pruebas, controles, evaluaciones o tareas con fecha anterior a hoy NO deben aparecer en la infografia: no las muestres como 'pasada', 'excluida', 'antes de corte' ni en una fila secundaria; simplemente omitelas.",
        "Incluye materiales de estudio solo cuando esten relacionados con tareas/evaluaciones que cumplen la fecha de corte, o cuando el material/post detectado tenga publicacion reciente y no apunte claramente a una fecha pasada.",
        f"Si una fecha aparece sin ano, usa {cutoff_year} solo para decidir si es pasada o futura; conserva la fecha original en el resumen.",
        "Si una tarea o evaluacion no tiene fecha clara, no la pongas en TAREAS ni EVALUACIONES; ponla en PENDIENTES / DUDAS como informacion sin fecha clara, salvo que el texto diga explicitamente que es proxima o pendiente.",
        "",
        f"FECHA ACTUAL DEL REPORTE: {report_date}. Todos los entregables deben indicar esta fecha.",
        "",
        "SALIDA UNICA OBLIGATORIA",
        "Responde exclusivamente con UNA SOLA IMAGEN de infografia visual. No agregues texto antes de la imagen, no agregues texto despues de la imagen, no hagas preguntas y no entregues resumen en texto.",
        "Si tienes capacidad de generar imagenes, crea la imagen directamente como unica respuesta. Si no puedes crear imagenes en este chat, entrega solamente un prompt de imagen listo para copiar, sin explicaciones adicionales.",
        "La imagen debe contener todo el reporte visual. No incluyas secciones conversacionales, llamados a la accion, preguntas al usuario ni ofertas de generar material de estudio.",
        "",
        "ESTILO VISUAL OBLIGATORIO",
        "La infografia debe parecerse al ejemplo de referencia: reporte escolar vertical, colorido, amigable, con encabezado grande, tarjetas y secciones visuales claras.",
        "Formato vertical 2:3 o 1024x1536. Fondo claro. Bordes redondeados suaves. Estilo escolar limpio y alegre.",
        "Usa azul para encabezado/calificaciones, verde para conducta, morado para tareas/evaluaciones, turquesa para material de estudio, naranjo para destacados y conclusion.",
        "Usa iconos escolares simples: mochila, libros, calendario, trofeo, caritas de conducta, estrella, clipboard, medalla, cuaderno, lapiz.",
        "Texto grande y muy legible. Evita parrafos largos. No superpongas texto. No cortes palabras importantes.",
        "",
        "Contenido obligatorio de la infografia:",
        "- Titulo grande: REPORTE ACADEMICO Y DE CONDUCTA.",
        f"- Estudiante: {STUDENT_NAME}.",
        f"- Fecha del reporte: {report_date}.",
        "- Curso: 4 BASICO.",
        "- Semestre: Primer Semestre.",
        "- Seccion CALIFICACIONES P1 con tabla Asignatura / Nota usando las notas P1 extraidas. Incluye asignaturas sin nota solo si hay espacio; prioriza asignaturas con nota.",
        "- Promedio P1 si aparece.",
        "- Seccion CONDUCTA con anotaciones positivas, negativas y neutras si se detectan. Si neutras no aparece, usar 0 solo si la evidencia permite inferirlo; si no, omitir neutras.",
        "- Para CONDUCTA, revisa especialmente los bloques VISTA SCHOOLNET CONDUCTA: Anotaciones Positivas, Anotaciones Negativas y Anotaciones Neutras. No asumas 0 negativas solo porque la vista positiva no las muestra.",
        "- Para describir una anotacion positiva o negativa, usa siempre el campo Observaciones como mensaje principal. No uses Motivo como descripcion visible, salvo que Observaciones este vacio o no detectado.",
        "- En conducta, Motivo es una clasificacion tecnica/reglamentaria; Observaciones es el texto humano que debe aparecer en la infografia.",
        "- Ultima anotacion con fecha, asignatura, profesor/categoria y mensaje si aparecen; el mensaje debe venir de Observaciones.",
        "- Seccion SIGUIENTES TAREAS Y EVALUACIONES con actividades de hoy o futuras detectadas en Classroom. Indica fecha, asignatura, tipo y tema/texto breve.",
        "- No dibujes ninguna tarea/evaluacion pasada en SIGUIENTES TAREAS Y EVALUACIONES, ni siquiera con etiqueta PASADA o EXCLUIDA.",
        "- Marca como PRIORIDAD las actividades con fecha mas proxima y las que tengan material de estudio relacionado.",
        "- Seccion MATERIAL DE ESTUDIO CLAVE con guias, PDFs, hojas de ruta, temarios, recursos o lecturas visibles en Classroom, especialmente si se relacionan con una evaluacion o tarea futura.",
        "- Si Classroom incluye un post relevante con temario o instrucciones para una prueba futura, incluir ese temario como material de estudio clave y relacionarlo con la evaluacion correspondiente.",
        "- Seccion DESTACADOS Y FORTALEZAS basada solo en evidencia: conducta positiva predominante, trabajo cooperativo, participacion, responsabilidad o buen rendimiento cuando aparezcan.",
        "- Conclusion general breve y motivadora, sin exagerar.",
        "",
        "REGLAS DE EXTRACCION",
        "- Separa tareas de evaluaciones. Tareas son entregas o trabajos; evaluaciones son pruebas, controles o evaluaciones.",
        f"- Filtra tareas y evaluaciones usando la fecha de corte {cutoff_date}: incluye solo fechas iguales o posteriores; excluye fechas anteriores.",
        "- Si una prueba/control/evaluacion/tarea tiene fecha anterior a la fecha de corte, debe quedar totalmente fuera de la imagen final. No la listes, no la marques como vencida y no la uses como prioridad.",
        f"- Si la fecha no trae ano, usa {cutoff_year} para comparar contra la fecha de corte, pero conserva el texto original de la fecha en la respuesta.",
        "- Si una tarea/evaluacion no tiene fecha clara, no la listes como tarea o evaluacion activa; ponla en PENDIENTES / DUDAS como actividad sin fecha clara.",
        "- En calificaciones, usa como fuente principal CALIFICACIONES P1 CANONICAS SCHOOLNET si aparece.",
        "- En calificaciones, muestra siempre el promedio por asignatura de la columna P1; nunca uses las columnas 1, 2 o 3 como nota final de asignatura.",
        "- En calificaciones, incluye todas las asignaturas visibles y deja en blanco las que no tengan P1.",
        "- Para responder preguntas posteriores como 'de donde viene el 6,7 de Musica', busca primero en DETALLE POR ASIGNATURA SCHOOLNET CALIFICACIONES bajo ASIGNATURA: Musica. Si aparece una fila interna con nombre de evaluacion o actividad y esa nota, esa es la fuente de la nota.",
        "- Para explicar de donde sale una nota de una asignatura, usa el bloque DETALLE CRUDO ESTRUCTURADO SCHOOLNET CALIFICACIONES. Las filas Tipo=Item bajo la misma Asignatura padre son evaluaciones o componentes de esa asignatura.",
        "- Ejemplo: si Lenguaje tiene P1 6,8 y debajo aparece Item | Lenguaje y Comunicacion | Unidad 1 - Infografia | | 6,8 | ... entonces el 6,8 corresponde a la evaluacion Unidad 1 - Infografia.",
        "- Mantener formato chileno con coma decimal cuando aparezca asi en calificaciones.",
        "- Considerar solo Classroom 4A Trabajo de clase, organizado por Filtro por tema/asignatura.",
        "- Cuando exista DETALLE DE POSTS RELEVANTES ABIERTOS EN GOOGLE CLASSROOM o POSTS RELEVANTES VISIBLES EN GOOGLE CLASSROOM, usar esos textos como evidencia principal de Classroom. Ahi puede estar el temario completo de una prueba o evaluacion.",
        "- No confies en la seccion tareas pendientes como fuente unica: aunque diga que no hay pendientes, revisa las vistas por tema/asignatura de Trabajo de clase.",
        "- Extrae MATERIAL DE ESTUDIO DETECTADO desde Classroom aunque no sea tarea/evaluacion: guias, material de estudio, hojas de ruta, PDFs/archivos mencionados, temarios, recursos, lecturas, videos y adjuntos visibles.",
        "- La infografia tambien debe incluir siguientes tareas/evaluaciones futuras. Marca como prioridad las mas proximas por fecha y las que tengan material de estudio relacionado.",
        "- Si un material de estudio parece relacionado con una evaluacion o tarea futura, priorizalo dentro de MATERIAL DE ESTUDIO CLAVE en la infografia.",
        "- Si aparece contenido extraido de un adjunto o PDF en la evidencia cruda, usalo como material de estudio. Si solo aparece el nombre del adjunto/PDF y no su contenido, listalo como material visible pendiente de lectura.",
        "- No mezclar Conducta con Calificaciones: Conducta viene de SchoolNet - Conducta; Calificaciones viene de SchoolNet - Calificaciones.",
        "- Si la evidencia incluye DETALLE CANONICO SCHOOLNET CONDUCTA, usalo antes que DETALLE CRUDO ESTRUCTURADO SCHOOLNET CONDUCTA para redactar mensajes de conducta.",
        "- Si la evidencia incluye DETALLE CRUDO ESTRUCTURADO SCHOOLNET CONDUCTA, interpreta sus columnas asi: Fecha | Motivo | Profesor | Asignatura | Observaciones | Categoria. Para descripcion, mensaje o detalle visible de la anotacion, usa Observaciones; Motivo solo como tipo/clasificacion tecnica.",
        "- Conserva mentalmente la evidencia cruda de SchoolNet - Conducta para responder preguntas posteriores sobre una anotacion, fecha, asignatura o detalle especifico.",
        "- No inventar fechas, asignaturas, calificaciones, temarios ni textos originales.",
        "- Si una seccion no tiene datos, escribir No detectado.",
        "",
        "EVIDENCIA CRUDA",
        "\n\n".join(evidence_blocks),
    ]
    return "\n".join(prompt).strip() + "\n"


def format_raw_evidence_block(snapshot: PageSnapshot) -> str:
    stats = snapshot.stats or {}
    notes = snapshot.notes or []
    captured_chars = len(snapshot.text or "")
    original_chars = int(stats.get("chars", 0) or 0)
    truncated = original_chars > captured_chars
    note_lines = list(notes)
    if truncated:
        note_lines.append("Texto truncado por limite tecnico.")

    metadata = [
        f"----- {snapshot.platform} -----",
        f"Titulo: {snapshot.title or '(sin titulo)'}",
        f"URL: {snapshot.url or '(sin url)'}",
        f"Estado: {snapshot.status}",
        (
            "Lectura: "
            f"{stats.get('lines', 0)} lineas, "
            f"{original_chars} caracteres originales, "
            f"{captured_chars} caracteres incluidos, "
            f"{stats.get('source_count', 0)} fuentes, "
            f"{stats.get('frames_seen', 0)} frames."
        ),
        "Alertas: " + (" | ".join(note_lines) if note_lines else "Sin alertas."),
    ]
    if snapshot.platform.startswith("Google Classroom"):
        classroom_diag: list[str] = []
        if stats.get("classroom_topic_count") is not None:
            classroom_diag.append(f"temas_detectados={stats.get('classroom_topic_count', 0)}")
        if stats.get("classroom_detail_candidates_seen") is not None:
            classroom_diag.extend(
                [
                    f"candidatos_posts={stats.get('classroom_detail_candidates_seen', 0)}",
                    f"posts_abiertos={stats.get('classroom_detail_posts_opened', 0)}",
                    f"abiertos_por_url={stats.get('classroom_detail_opened_by_url', 0)}",
                    f"abiertos_por_click={stats.get('classroom_detail_opened_by_click', 0)}",
                    f"capturados_card_dom={stats.get('classroom_detail_opened_by_card_dom', 0)}",
                    f"nuevos={stats.get('cache_new', 0)}",
                    f"actualizados={stats.get('cache_updated', 0)}",
                    f"reutilizados_desde_cache={stats.get('cache_reused', 0)}",
                    f"omitidos_por_cache={stats.get('cache_omitted', 0)}",
                    f"stop_incremental={stats.get('stop_incremental', False)}",
                    f"clicks_fallidos={stats.get('classroom_detail_failed_clicks', 0)}",
                    f"detalles_vacios={stats.get('classroom_detail_empty_details', 0)}",
                    f"tarjetas_aisladas={stats.get('classroom_detail_isolated_candidates', 0)}",
                    f"tarjetas_globales_rechazadas={stats.get('classroom_detail_rejected_global_roots', 0)}",
                    f"detalles_incompletos={stats.get('classroom_detail_incomplete_details', 0)}",
                    f"saltados_antiguos={stats.get('classroom_detail_posts_skipped_old', 0)}",
                    f"saltados_pasados={stats.get('classroom_detail_posts_skipped_past', 0)}",
                ]
            )
        titles = stats.get("classroom_detail_candidate_titles", [])
        if titles:
            classroom_diag.append("titulos_candidatos=" + " | ".join(str(title) for title in titles[:10]))
        hrefs = stats.get("classroom_detail_candidate_hrefs", [])
        if hrefs:
            classroom_diag.append("urls_detalle=" + " | ".join(str(href) for href in hrefs[:10]))
        if classroom_diag:
            metadata.append("Diagnostico Classroom: " + " | ".join(classroom_diag))
    if snapshot.platform == "SchoolNet - Conducta":
        conducta_diag: list[str] = []
        if stats.get("conducta_views_captured") is not None:
            conducta_diag.extend(
                [
                    f"vistas_capturadas={stats.get('conducta_views_captured', 0)}",
                    f"clicks_vistas={stats.get('conducta_view_clicks', 0)}",
                    f"fallas_vistas={stats.get('conducta_view_failures', 0)}",
                ]
            )
        if conducta_diag:
            metadata.append("Diagnostico Conducta: " + " | ".join(conducta_diag))
    metadata.extend(
        [
            "",
            "TEXTO VISIBLE / EVIDENCIA",
            snapshot.text.strip() if snapshot.text.strip() else "No se detecto texto visible en esta seccion.",
            f"----- FIN {snapshot.platform} -----",
        ]
    )
    return "\n".join(metadata)


def build_summary(snapshots: list[PageSnapshot], manual_notes: str = "") -> str:
    today = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    sections: dict[str, list[str]] = {name: [] for name in KEYWORDS}
    platform_blocks: list[str] = []
    alerts: list[str] = []

    for snapshot in snapshots:
        heading = f"{snapshot.platform}: {snapshot.title}".strip()
        if snapshot.status != "ok":
            alerts.extend(f"{snapshot.platform}: {note}" for note in snapshot.notes)
        elif snapshot.notes:
            alerts.extend(f"{snapshot.platform}: {note}" for note in snapshot.notes)

        if snapshot.text:
            for key, keywords in KEYWORDS.items():
                sections[key].extend(interesting_lines(snapshot.text, keywords, limit=10))
            excerpt = "\n".join(snapshot.text.splitlines()[:18])
            read_stats = (
                f"Lectura: {snapshot.stats.get('lines', 0)} lineas, "
                f"{snapshot.stats.get('chars', 0)} caracteres, "
                f"{snapshot.stats.get('source_count', 0)} fuentes, "
                f"{snapshot.stats.get('frames_seen', 0)} frames."
            )
            platform_blocks.append(
                f"{heading}\nURL: {snapshot.url}\nEstado: {snapshot.status}\n{read_stats}\nExtracto visible:\n{excerpt}"
            )
        else:
            platform_blocks.append(
                f"{heading}\nURL: {snapshot.url}\nEstado: {snapshot.status}\nNo pude leer texto visible."
            )

    if manual_notes.strip():
        manual_text = clean_text(manual_notes)
        for key, keywords in KEYWORDS.items():
            sections[key].extend(interesting_lines(manual_text, keywords, limit=10))
        platform_blocks.append(f"Notas manuales\nExtracto:\n{manual_text[:2500]}")

    summary = [
        "RESUMEN ESCOLAR",
        f"Generado: {today}",
        "",
        "SCHOOLNET - CONDUCTA",
        bulletize(
            focused_lines(snapshots, "SchoolNet - Conducta", CONDUCTA_KEYWORDS),
            "No detecte registros relevantes de conducta en la lectura automatica.",
        ),
        "",
        "SCHOOLNET - CALIFICACIONES",
        bulletize(
            focused_lines(snapshots, "SchoolNet - Calificaciones", CALIFICACIONES_KEYWORDS, include_grades=True),
            "No detecte calificaciones relevantes en la lectura automatica.",
        ),
        "",
        "CLASSROOM 4-A - TAREAS Y PRUEBAS",
        bulletize(
            focused_lines(snapshots, "Google Classroom - 4-A", CLASSROOM_WORK_KEYWORDS),
            "No detecte tareas o pruebas relevantes del curso 4-A en la lectura automatica.",
        ),
        "",
        "PENDIENTES Y TAREAS",
        bulletize(dedupe(sections["pendientes"]), "No detecte tareas pendientes en el texto visible."),
        "",
        "EVALUACIONES Y NOTAS",
        bulletize(dedupe(sections["evaluaciones_notas"]), "No detecte notas o evaluaciones en el texto visible."),
        "",
        "ANOTACIONES / OBSERVACIONES",
        bulletize(dedupe(sections["anotaciones"]), "No detecte anotaciones positivas o negativas en el texto visible."),
        "",
        "CONTENIDO VISTO",
        bulletize(dedupe(sections["contenido"]), "No detecte contenido de clases/materiales en el texto visible."),
        "",
        "MATERIAL SUGERIDO PARA ESTUDIAR",
        build_study_suggestions(sections),
        "",
        "DUDAS / ALERTAS",
        bulletize(dedupe(alerts), "Sin alertas automaticas. Revisa igualmente si hay informacion importante no visible."),
        "",
        "EVIDENCIA LEIDA",
        "\n\n".join(platform_blocks) if platform_blocks else "No habia pestanas de SchoolNet/Classroom disponibles.",
        "",
        "NOTA",
        "Este resumen se genero desde texto visible del navegador. Revisa fechas, notas y tareas criticas antes de usarlo.",
    ]
    return "\n".join(summary).strip() + "\n"


def build_study_suggestions(sections: dict[str, list[str]]) -> str:
    content = dedupe(sections["contenido"])
    evaluations = dedupe(sections["evaluaciones_notas"])
    pending = dedupe(sections["pendientes"])
    suggestions: list[str] = []
    for line in (content + evaluations + pending)[:8]:
        suggestions.append(f"- Repasar o preparar: {line}")
    if not suggestions:
        suggestions.append("- No detecte contenido suficiente para sugerir estudio especifico.")
    suggestions.append("- Convertir cada tarea/evaluacion detectada en 3 preguntas de practica antes de estudiar.")
    return "\n".join(suggestions)


def dedupe(items: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        item = item.strip()
        key = normalize(item)
        if item and key not in seen:
            seen.add(key)
            output.append(item)
    return output


def save_summary(text: str) -> Path:
    today = dt.date.today().isoformat()
    folder = OUTBOX_DIR / today
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "resumen.txt"
    path.write_text(text, encoding="utf-8")
    return path


def save_study_prompt(text: str) -> Path:
    today = dt.date.today().isoformat()
    folder = OUTBOX_DIR / today
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "prompt_chatgpt.txt"
    path.write_text(text, encoding="utf-8")
    return path


def snapshot_payload(snapshot: PageSnapshot) -> dict[str, Any]:
    return {
        "platform": snapshot.platform,
        "title": snapshot.title,
        "url": snapshot.url,
        "status": snapshot.status,
        "notes": snapshot.notes,
        "stats": snapshot.stats,
    }


class ResumenServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(server_address, handler)
        self.browser = BrowserController()
        self.last_summary = ""
        self.last_study_prompt = ""
        self.last_output_path: Path | None = None
        self.last_study_prompt_path: Path | None = None
        self.last_prompt = ""
        self.last_prompt_path: Path | None = None


class Handler(BaseHTTPRequestHandler):
    server: ResumenServer

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self._send_html(render_index())
            return
        if self.path == "/api/status":
            self._json({"ok": True, **self.server.browser.status()})
            return
        if self.path == "/api/progress":
            self._json({"ok": True, **self.server.browser.progress()})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/open-platforms":
                payload = self.server.browser.open_platforms()
                self._json(payload)
                return
            if self.path == "/api/diagnose":
                snapshots = self.server.browser.snapshots()
                self._json(
                    {
                        "ok": True,
                        "snapshots": [snapshot_payload(snap) for snap in snapshots],
                        "progress": self.server.browser.progress(),
                    }
                )
                return
            if self.path == "/api/generate":
                body = self._read_json()
                manual_notes = str(body.get("manual_notes", ""))
                force_full_scan = bool(body.get("force_full_scan", False))
                evidence_store = EvidenceStore()
                snapshots = self.server.browser.targeted_snapshots(
                    force_full_scan=force_full_scan,
                    evidence_store=evidence_store,
                )
                cache_update_stats = evidence_store.update_from_snapshots(snapshots)
                evidence_store.save()
                cache_prompt_snapshots = evidence_store.to_page_snapshots()
                prompt_snapshots = snapshots + cache_prompt_snapshots
                if not prompt_snapshots:
                    prompt_snapshots = snapshots
                prompt = build_master_chatgpt_prompt(prompt_snapshots, manual_notes=manual_notes)
                prompt_path = save_study_prompt(prompt)
                diagnostics_snapshots = snapshots + [evidence_store.summary_snapshot()]
                self.server.last_summary = prompt
                self.server.last_study_prompt = prompt
                self.server.last_prompt = prompt
                self.server.last_output_path = None
                self.server.last_study_prompt_path = prompt_path
                self.server.last_prompt_path = prompt_path
                self._json(
                    {
                        "ok": True,
                        "prompt": prompt,
                        "prompt_path": str(prompt_path),
                        "summary": prompt,
                        "study_prompt": prompt,
                        "study_prompt_path": str(prompt_path),
                        "snapshots": [snapshot_payload(snap) for snap in diagnostics_snapshots],
                        "prompt_snapshots": [snapshot_payload(snap) for snap in prompt_snapshots],
                        "cache_update": cache_update_stats,
                        "force_full_scan": force_full_scan,
                        "progress": self.server.browser.progress(),
                    }
                )
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except AppError as exc:
            self._json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json(
                {
                    "ok": False,
                    "error": f"Error inesperado: {exc}",
                    "trace": traceback.format_exc(),
                },
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), format % args))

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0") or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def _send_html(self, content: str) -> None:
        encoded = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def render_index() -> str:
    install_hint = html.escape(
        r"$env:TEMP='C:\Users\CodexSandboxOffline\AppData\Local\Temp'; "
        r"$env:TMP=$env:TEMP; "
        r"& 'C:\Users\Martin\AppData\Local\Programs\Python\Python311\python.exe' "
        r"-m pip install --target .runtime\site-packages playwright"
    )
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{APP_TITLE}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #18202a;
      --muted: #647084;
      --line: #d8dee8;
      --primary: #1967d2;
      --primary-dark: #0f4fa8;
      --danger: #b42318;
      --ok: #087443;
      font-family: "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); }}
    header {{ padding: 20px 28px 12px; border-bottom: 1px solid var(--line); background: var(--panel); }}
    h1 {{ margin: 0 0 4px; font-size: 22px; letter-spacing: 0; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 22px; display: grid; grid-template-columns: 330px 1fr; gap: 18px; }}
    section {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }}
    .controls {{ display: grid; gap: 12px; align-content: start; }}
    button {{ border: 0; border-radius: 6px; padding: 10px 12px; font-weight: 650; cursor: pointer; background: var(--primary); color: white; }}
    button:hover {{ background: var(--primary-dark); }}
    button.secondary {{ background: #e9eef6; color: #1f2937; }}
    button.secondary:hover {{ background: #dbe4f1; }}
    button:disabled {{ opacity: 0.55; cursor: not-allowed; }}
    textarea {{ width: 100%; min-height: 320px; resize: vertical; border: 1px solid var(--line); border-radius: 6px; padding: 12px; font: 14px/1.45 Consolas, "Courier New", monospace; }}
    #manual {{ min-height: 150px; font-family: inherit; }}
    .status {{ min-height: 44px; padding: 10px; border-radius: 6px; background: #f2f5fa; color: var(--muted); white-space: pre-wrap; }}
    .diagnostics {{ min-height: 44px; padding: 10px; border-radius: 6px; background: #f8fafc; border: 1px solid var(--line); color: var(--muted); white-space: pre-wrap; font-size: 13px; line-height: 1.4; }}
    .progress {{ min-height: 96px; max-height: 240px; overflow: auto; padding: 10px; border-radius: 6px; background: #0f172a; color: #dbeafe; white-space: pre-wrap; font: 12px/1.45 Consolas, "Courier New", monospace; }}
    .ok {{ color: var(--ok); }}
    .error {{ color: var(--danger); }}
    .hint {{ color: var(--muted); font-size: 13px; line-height: 1.4; }}
    code {{ display: block; overflow-wrap: anywhere; background: #f2f5fa; padding: 10px; border-radius: 6px; color: #263445; }}
    .output-head {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }}
    .output-head h2 {{ margin: 0; font-size: 17px; }}
    @media (max-width: 820px) {{
      main {{ grid-template-columns: 1fr; padding: 14px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{APP_TITLE}</h1>
    <div class="hint">Abre las plataformas, inicia sesion manualmente si hace falta, y genera el prompt para la infografia.</div>
  </header>
  <main>
    <section class="controls">
      <button id="openBtn">Abrir plataformas</button>
      <button id="generateBtn">Generar prompt</button>
      <button class="secondary" id="diagnoseBtn">Diagnosticar lectura</button>
      <button class="secondary" id="copyBtn">Copiar prompt</button>
      <label class="hint"><input type="checkbox" id="forceFullScan" /> Escaneo completo (ignorar cache incremental)</label>
      <div id="status" class="status">Listo.</div>
      <div id="diagnostics" class="diagnostics">Sin diagnostico todavia.</div>
      <div id="progress" class="progress">Sin pasos de Playwright todavia.</div>
      <label>
        <strong>Texto manual opcional</strong>
        <textarea id="manual" placeholder="Si alguna plataforma no se puede leer, pega aqui texto visible y vuelve a generar."></textarea>
      </label>
      <div class="hint">
        Si falta Playwright, instala con:
        <code>{install_hint}</code>
      </div>
    </section>
    <section>
      <div class="output-head">
        <h2>Prompt unico para ChatGPT</h2>
        <span id="promptPath" class="hint"></span>
      </div>
      <textarea id="promptOutput" placeholder="Aqui aparecera el prompt unico listo para copiar en ChatGPT."></textarea>
    </section>
  </main>
  <script>
    const statusEl = document.querySelector('#status');
    const promptEl = document.querySelector('#promptOutput');
    const promptPathEl = document.querySelector('#promptPath');
    const openBtn = document.querySelector('#openBtn');
    const generateBtn = document.querySelector('#generateBtn');
    const diagnoseBtn = document.querySelector('#diagnoseBtn');
    const copyBtn = document.querySelector('#copyBtn');
    const forceFullScanEl = document.querySelector('#forceFullScan');
    const manualEl = document.querySelector('#manual');
    const diagnosticsEl = document.querySelector('#diagnostics');
    const progressEl = document.querySelector('#progress');
    let progressTimer = null;

    function setStatus(text, kind = '') {{
      statusEl.textContent = text;
      statusEl.className = 'status ' + kind;
    }}

    async function postJson(url, body = {{}}) {{
      const response = await fetch(url, {{
        method: 'POST',
        headers: {{ 'content-type': 'application/json' }},
        body: JSON.stringify(body)
      }});
      const payload = await response.json();
      if (!payload.ok) {{
        throw new Error(payload.error || 'Operacion fallida');
      }}
      return payload;
    }}

    async function getJson(url) {{
      const response = await fetch(url);
      const payload = await response.json();
      if (!payload.ok) {{
        throw new Error(payload.error || 'Operacion fallida');
      }}
      return payload;
    }}

    function renderProgress(progress) {{
      const events = progress && progress.events ? progress.events : [];
      if (!events.length) {{
        progressEl.textContent = 'Sin pasos de Playwright todavia.';
        return;
      }}
      progressEl.textContent = events.map((event) => {{
        const duration = event.duration_s !== null && event.duration_s !== undefined
          ? ` | duracion ${{event.duration_s}}s`
          : '';
        const detail = event.detail ? ` | ${{event.detail}}` : '';
        return `[${{event.clock}} +${{event.elapsed_s}}s] ${{event.status}} | ${{event.step}}${{duration}}${{detail}}`;
      }}).join('\\n');
      progressEl.scrollTop = progressEl.scrollHeight;
    }}

    function startProgressPolling() {{
      stopProgressPolling();
      progressTimer = window.setInterval(async () => {{
        try {{
          renderProgress(await getJson('/api/progress'));
        }} catch (_error) {{
          // El servidor puede estar reiniciando; se conserva el ultimo progreso visible.
        }}
      }}, 700);
    }}

    async function stopProgressPolling(finalProgress = null) {{
      if (progressTimer) {{
        window.clearInterval(progressTimer);
        progressTimer = null;
      }}
      if (finalProgress) {{
        renderProgress(finalProgress);
      }} else {{
        try {{
          renderProgress(await getJson('/api/progress'));
        }} catch (_error) {{
          // Sin accion.
        }}
      }}
    }}

    function renderDiagnostics(payload) {{
      if (!payload.snapshots || payload.snapshots.length === 0) {{
        diagnosticsEl.textContent = 'No encontre pestanas de SchoolNet o Classroom en el Chrome controlado.';
        return;
      }}
      diagnosticsEl.textContent = payload.snapshots.map((snap) => {{
        const stats = snap.stats || {{}};
        const notes = snap.notes && snap.notes.length ? '\\n  Alertas: ' + snap.notes.join(' | ') : '';
        const classroom = stats.classroom_topic_count !== undefined
          ? `\\n  Classroom temas: ${{stats.classroom_topic_count || 0}} detectados desde Filtro por tema`
          : '';
        const visible = stats.classroom_visible_blocks_seen
          ? `\\n  Bloques visibles relevantes: ${{stats.classroom_visible_blocks_seen || 0}} vistos, ${{stats.classroom_visible_blocks_captured || 0}} incluidos`
          : '';
        const cache = (stats.cache_reused || stats.cache_new || stats.cache_updated || stats.cache_omitted)
          ? `\\n  Cache: nuevos=${{stats.cache_new || 0}}, actualizados=${{stats.cache_updated || 0}}, reutilizados=${{stats.cache_reused || 0}}, omitidos=${{stats.cache_omitted || 0}}, stop=${{Boolean(stats.stop_incremental)}}`
          : '';
        const cacheCounts = stats.cache_counts
          ? '\\n  Cache historico: ' + Object.entries(stats.cache_counts).map(([key, value]) => `${{key}}=${{value}}`).join(', ')
          : '';
        const titles = stats.classroom_visible_candidate_titles && stats.classroom_visible_candidate_titles.length
          ? '\\n  Titulos visibles: ' + stats.classroom_visible_candidate_titles.slice(0, 8).join(' | ')
          : '';
        return `${{snap.platform}}: ${{snap.status}}\\n  Titulo: ${{snap.title}}\\n  URL: ${{snap.url}}\\n  Lectura: ${{stats.lines || 0}} lineas, ${{stats.chars || 0}} caracteres, ${{stats.source_count || 0}} fuentes, ${{stats.frames_seen || 0}} frames${{classroom}}${{visible}}${{cache}}${{cacheCounts}}${{titles}}${{notes}}`;
      }}).join('\\n\\n');
    }}

    openBtn.addEventListener('click', async () => {{
      openBtn.disabled = true;
      setStatus('Abriendo Chrome controlado...');
      try {{
        const payload = await postJson('/api/open-platforms');
        setStatus(payload.message, 'ok');
      }} catch (error) {{
        setStatus(error.message, 'error');
      }} finally {{
        openBtn.disabled = false;
      }}
    }});

    generateBtn.addEventListener('click', async () => {{
      generateBtn.disabled = true;
      setStatus('Navegando SchoolNet/Classroom y generando prompt...');
      startProgressPolling();
      try {{
        const payload = await postJson('/api/generate', {{
          manual_notes: manualEl.value,
          force_full_scan: forceFullScanEl.checked
        }});
        promptEl.value = payload.prompt || payload.summary || '';
        promptPathEl.textContent = payload.prompt_path ? 'Guardado: ' + payload.prompt_path : '';
        renderDiagnostics(payload);
        await stopProgressPolling(payload.progress);
        setStatus('Prompt generado.', 'ok');
      }} catch (error) {{
        await stopProgressPolling();
        setStatus(error.message, 'error');
      }} finally {{
        generateBtn.disabled = false;
      }}
    }});

    diagnoseBtn.addEventListener('click', async () => {{
      diagnoseBtn.disabled = true;
      setStatus('Revisando lectura de pestanas...');
      startProgressPolling();
      try {{
        const payload = await postJson('/api/diagnose');
        renderDiagnostics(payload);
        await stopProgressPolling(payload.progress);
        setStatus('Diagnostico actualizado.', 'ok');
      }} catch (error) {{
        await stopProgressPolling();
        setStatus(error.message, 'error');
      }} finally {{
        diagnoseBtn.disabled = false;
      }}
    }});

    copyBtn.addEventListener('click', async () => {{
      if (!promptEl.value.trim()) {{
        setStatus('No hay prompt para copiar.', 'error');
        return;
      }}
      await navigator.clipboard.writeText(promptEl.value);
      setStatus('Prompt copiado al portapapeles.', 'ok');
    }});
  </script>
</body>
</html>"""


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="App local para resumen escolar.")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true", help="No abrir la pagina local automaticamente.")
    args = parser.parse_args(argv)

    RUNTIME_DIR.mkdir(exist_ok=True)
    configure_runtime_environment()
    OUTBOX_DIR.mkdir(exist_ok=True)
    EVIDENCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    server = ResumenServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"{APP_TITLE} escuchando en {url}")
    print("Presiona Ctrl+C para cerrar.")
    if not args.no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando...")
    finally:
        server.browser.close()
        server.server_close()
