/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import pkg from './package.json' with { type: 'json' };

export default defineConfig(({ mode }) => ({
	plugins: [tailwindcss(), sveltekit()],
	define: { __APP_VERSION__: JSON.stringify(pkg.version) },
	resolve: mode === 'test' ? { conditions: ['browser'] } : undefined,
	test: {
		environment: 'node',
		include: ['src/**/*.{test,spec}.ts']
	}
}));
