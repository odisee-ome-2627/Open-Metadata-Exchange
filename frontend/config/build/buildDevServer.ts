import type { Configuration as DevServerConfiguration } from 'webpack-dev-server';
import { BuildOptions } from './types/config';

export function buildDevServer(options: BuildOptions): DevServerConfiguration {
    return {
        port: options.port,
        open: true,
        historyApiFallback: true,
        hot: true,
	allowedHosts: "all",
        // Forward API calls to the FastAPI server from `docker compose up`.
        proxy: {
            '/api': { target: process.env.OME_API_URL || 'http://localhost:5001' },
        },
    };
}
