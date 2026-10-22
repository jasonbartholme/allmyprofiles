"""baseline schema (squashed) with catalogue seed data

Revision ID: 0001baseline
Revises: 
Create Date: 2026-10-13 23:17:01.411924

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0001baseline'
down_revision = None
branch_labels = None
depends_on = None

# Catalogue seed data captured from the development database when the
# earlier migrations were squashed into this baseline.
SEED_DATA = {'link_categories': [{'id': 1,
                      'name': 'Gaming & Dev',
                      'icon_code': 'controller',
                      'bg_color': '#e7f1e7',
                      'text_color': '#1e4620',
                      'sort_order': 0,
                      'is_deleted': 0,
                      'created_at': '2026-09-30 01:21:54.386938',
                      'updated_at': None,
                      'is_adult': 0},
                     {'id': 2,
                      'name': 'Content',
                      'icon_code': 'play-btn',
                      'bg_color': '#fdeaea',
                      'text_color': '#7a1f1f',
                      'sort_order': 1,
                      'is_deleted': 0,
                      'created_at': '2026-09-30 01:21:54.386957',
                      'updated_at': None,
                      'is_adult': 0},
                     {'id': 3,
                      'name': 'Social',
                      'icon_code': 'people',
                      'bg_color': '#e8f0fe',
                      'text_color': '#1a4f8b',
                      'sort_order': 2,
                      'is_deleted': 0,
                      'created_at': '2026-09-30 01:21:54.386958',
                      'updated_at': None,
                      'is_adult': 0},
                     {'id': 4,
                      'name': 'Store',
                      'icon_code': 'bag',
                      'bg_color': '#fff3e0',
                      'text_color': '#8a4b08',
                      'sort_order': 3,
                      'is_deleted': 0,
                      'created_at': '2026-09-30 01:21:54.386959',
                      'updated_at': None,
                      'is_adult': 0},
                     {'id': 5,
                      'name': 'Portfolio',
                      'icon_code': 'briefcase',
                      'bg_color': '#ede7f6',
                      'text_color': '#4527a0',
                      'sort_order': 4,
                      'is_deleted': 0,
                      'created_at': '2026-09-30 01:21:54.386960',
                      'updated_at': None,
                      'is_adult': 0},
                     {'id': 6,
                      'name': 'Music',
                      'icon_code': 'music-note',
                      'bg_color': '#e0f2f1',
                      'text_color': '#00695c',
                      'sort_order': 5,
                      'is_deleted': 0,
                      'created_at': '2026-09-30 01:21:54.386961',
                      'updated_at': None,
                      'is_adult': 0},
                     {'id': 7,
                      'name': 'Other',
                      'icon_code': 'tag',
                      'bg_color': '#f8f9fa',
                      'text_color': '#212529',
                      'sort_order': 6,
                      'is_deleted': 0,
                      'created_at': '2026-09-30 01:21:54.386962',
                      'updated_at': None,
                      'is_adult': 0},
                     {'id': 8,
                      'name': 'Adult Demo',
                      'icon_code': 'shield-lock',
                      'bg_color': '#4d0a0a',
                      'text_color': '#ffffff',
                      'sort_order': 0,
                      'is_deleted': 0,
                      'created_at': '2026-10-07 18:54:56.709560',
                      'updated_at': None,
                      'is_adult': 1}],
 'link_sources': [{'id': 1,
                   'name': 'GitHub',
                   'domains': 'github.com,gitlab.com',
                   'url': 'https://github.com/',
                   'profile_pattern': 'https://github.com/{handle}',
                   'bg_color': '#24292e',
                   'text_color': '#ffffff',
                   'border_color': '#24292e',
                   'icon_code': 'github',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Gaming & Dev',
                   'sort_order': 0,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751871',
                   'updated_at': None,
                   'description': "GitHub is the world's largest home for open source code, where "
                                  'developers host repositories, review pull requests, and ship '
                                  'software together. Link your profile here to showcase the '
                                  'projects, contributions, and organisations that define your '
                                  'engineering work.',
                   'home_url': 'https://github.com/'},
                  {'id': 2,
                   'name': 'Steam',
                   'domains': 'store.steampowered.com,steamcommunity.com',
                   'url': 'https://store.steampowered.com/',
                   'profile_pattern': 'https://store.steampowered.com/{handle}',
                   'bg_color': '#1b2838',
                   'text_color': '#c7d5e0',
                   'border_color': '#171a21',
                   'icon_code': 'steam',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Gaming & Dev',
                   'sort_order': 1,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751878',
                   'updated_at': None,
                   'description': "Steam is Valve's PC gaming storefront and social network, "
                                  'bundling a vast game library, community hubs, reviews, and '
                                  'trading cards into one client. Share your Steam profile to let '
                                  'visitors see the games you play and the badges you have earned.',
                   'home_url': 'https://store.steampowered.com/'},
                  {'id': 3,
                   'name': 'Xbox Live',
                   'domains': 'xbox.com,xboxlive.com',
                   'url': 'https://xbox.com/',
                   'profile_pattern': 'https://xbox.com/{handle}',
                   'bg_color': '#107c10',
                   'text_color': '#ffffff',
                   'border_color': '#0b5a0b',
                   'icon_code': 'controller',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Gaming & Dev',
                   'sort_order': 2,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751880',
                   'updated_at': None,
                   'description': 'Xbox Live connects console and PC players through gamertags, '
                                  "achievements, parties, and cloud saves across Microsoft's "
                                  'gaming ecosystem. A linked profile lets friends find your gamer '
                                  'score, favourite titles, and multiplayer history at a glance.',
                   'home_url': 'https://xbox.com/'},
                  {'id': 4,
                   'name': 'PlayStation Network',
                   'domains': 'playstation.com',
                   'url': 'https://playstation.com/',
                   'profile_pattern': 'https://playstation.com/{handle}',
                   'bg_color': '#00439c',
                   'text_color': '#ffffff',
                   'border_color': '#002d6b',
                   'icon_code': 'controller',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Gaming & Dev',
                   'sort_order': 3,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751882',
                   'updated_at': None,
                   'description': "PlayStation Network is Sony's online service for PlayStation "
                                  'consoles, hosting friend lists, trophies, plus subscriptions, '
                                  'and digital game libraries. Linking your PSN profile gives '
                                  'visitors a verified window onto the games you play and the '
                                  'trophies you collect.',
                   'home_url': 'https://playstation.com/'},
                  {'id': 5,
                   'name': 'Discord',
                   'domains': 'discord.gg,discord.com',
                   'url': 'https://discord.gg/',
                   'profile_pattern': 'https://discord.gg/{handle}',
                   'bg_color': '#5865f2',
                   'text_color': '#ffffff',
                   'border_color': '#4752c4',
                   'icon_code': 'discord',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Social',
                   'sort_order': 4,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751883',
                   'updated_at': None,
                   'description': 'Discord is a voice, video, and text chat platform built around '
                                  'servers that communities run themselves, from gaming guilds to '
                                  'study groups and dev teams. Add your invite or profile link so '
                                  'people can join the conversations you are already part of.',
                   'home_url': 'https://discord.gg/'},
                  {'id': 6,
                   'name': 'YouTube',
                   'domains': 'youtube.com,youtu.be',
                   'url': 'https://youtube.com/',
                   'profile_pattern': 'https://youtube.com/{handle}',
                   'bg_color': '#ff0000',
                   'text_color': '#ffffff',
                   'border_color': '#cc0000',
                   'icon_code': 'youtube',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Content',
                   'sort_order': 5,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751884',
                   'updated_at': None,
                   'description': 'YouTube is the dominant video platform for creators, educators, '
                                  'musicians, and broadcasters, reaching billions of viewers each '
                                  'month. Linking your channel lets audiences jump straight to '
                                  'your uploads, Shorts, live streams, and subscriber milestones '
                                  'without searching.',
                   'home_url': 'https://youtube.com/'},
                  {'id': 7,
                   'name': 'Twitch',
                   'domains': 'twitch.tv',
                   'url': 'https://twitch.tv/',
                   'profile_pattern': 'https://twitch.tv/{handle}',
                   'bg_color': '#9146ff',
                   'text_color': '#ffffff',
                   'border_color': '#772ce8',
                   'icon_code': 'twitch',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Content',
                   'sort_order': 6,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751885',
                   'updated_at': None,
                   'description': "Twitch is Amazon's live-streaming network, best known for "
                                  'gaming broadcasts but also home to music, art, cooking, and '
                                  'just-chatting creators. A linked channel shows your followers '
                                  'count, streaming schedule, and clips so visitors know exactly '
                                  'when to catch you live.',
                   'home_url': 'https://twitch.tv/'},
                  {'id': 8,
                   'name': 'Vimeo',
                   'domains': 'vimeo.com',
                   'url': 'https://vimeo.com/',
                   'profile_pattern': 'https://vimeo.com/{handle}',
                   'bg_color': '#1ab7ea',
                   'text_color': '#ffffff',
                   'border_color': '#1494bd',
                   'icon_code': 'vimeo',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Content',
                   'sort_order': 7,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751886',
                   'updated_at': None,
                   'description': 'Vimeo is a video hosting platform favoured by filmmakers, '
                                  'animators, and brands for its ad-free player, privacy controls, '
                                  'and high-quality playback standards. Link your account to '
                                  'present curated reels and portfolio pieces in a clean, '
                                  'professional setting.',
                   'home_url': 'https://vimeo.com/'},
                  {'id': 9,
                   'name': 'Medium',
                   'domains': 'medium.com',
                   'url': 'https://medium.com/',
                   'profile_pattern': 'https://medium.com/{handle}',
                   'bg_color': '#12100e',
                   'text_color': '#ffffff',
                   'border_color': '#000000',
                   'icon_code': 'medium',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Content',
                   'sort_order': 8,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751888',
                   'updated_at': None,
                   'description': 'Medium is a publishing platform where independent writers, '
                                  'engineers, and analysts publish essays and serialized stories '
                                  'to large built-in audiences. Linking your profile highlights '
                                  'your claps, publications, and reading interests, giving readers '
                                  'a direct path to your latest articles.',
                   'home_url': 'https://medium.com/'},
                  {'id': 10,
                   'name': 'Substack',
                   'domains': 'substack.com',
                   'url': 'https://substack.com/',
                   'profile_pattern': 'https://substack.com/{handle}',
                   'bg_color': '#ff6719',
                   'text_color': '#ffffff',
                   'border_color': '#d95511',
                   'icon_code': 'envelope-paper',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': '#Content',
                   'sort_order': 9,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751889',
                   'updated_at': None,
                   'description': 'Substack lets journalists, authors, and specialists run paid or '
                                  'free email newsletters with a simple subscription model and '
                                  'portable audience. Sharing your publication link helps readers '
                                  'sign up directly and discover the other writers you recommend '
                                  'on the network.',
                   'home_url': 'https://substack.com/'},
                  {'id': 11,
                   'name': 'Spotify',
                   'domains': 'spotify.com',
                   'url': 'https://spotify.com/',
                   'profile_pattern': 'https://spotify.com/{handle}',
                   'bg_color': '#1db954',
                   'text_color': '#0b2a14',
                   'border_color': '#169c46',
                   'icon_code': 'spotify',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Music',
                   'sort_order': 10,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751890',
                   'updated_at': None,
                   'description': 'Spotify is the leading music streaming service, hosting tens of '
                                  'millions of tracks, podcasts, and algorithmic playlists. '
                                  'Linking your artist page or public profile lets fans stream '
                                  'your releases instantly and follow your listening taste, '
                                  'playlists, and monthly listeners.',
                   'home_url': 'https://spotify.com/'},
                  {'id': 12,
                   'name': 'Apple Music',
                   'domains': 'music.apple.com',
                   'url': 'https://music.apple.com/',
                   'profile_pattern': 'https://music.apple.com/{handle}',
                   'bg_color': '#fa243c',
                   'text_color': '#ffffff',
                   'border_color': '#c91e31',
                   'icon_code': 'apple-music',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Music',
                   'sort_order': 11,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751891',
                   'updated_at': None,
                   'description': 'Apple Music combines lossless streaming, radio, and deep '
                                  'integration across Apple devices, with artist pages that '
                                  'surface discographies and verified profiles. Link it to send '
                                  'listeners straight to your album releases, singles, and curated '
                                  'playlists in their native app.',
                   'home_url': 'https://music.apple.com/'},
                  {'id': 13,
                   'name': 'SoundCloud',
                   'domains': 'soundcloud.com',
                   'url': 'https://soundcloud.com/',
                   'profile_pattern': 'https://soundcloud.com/{handle}',
                   'bg_color': '#ff5500',
                   'text_color': '#ffffff',
                   'border_color': '#d64800',
                   'icon_code': 'cloud-fill',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Music',
                   'sort_order': 12,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751893',
                   'updated_at': None,
                   'description': 'SoundCloud is a creator-first audio platform where DJs, '
                                  'producers, and podcasters upload mixes and demos directly to '
                                  'listeners without label gatekeeping. Your linked profile '
                                  'showcases track counts, plays, and reposts, making it easy for '
                                  'fans to hear your newest experiments.',
                   'home_url': 'https://soundcloud.com/'},
                  {'id': 14,
                   'name': 'Bandcamp',
                   'domains': 'bandcamp.com',
                   'url': 'https://bandcamp.com/',
                   'profile_pattern': 'https://bandcamp.com/{handle}',
                   'bg_color': '#629aa9',
                   'text_color': '#ffffff',
                   'border_color': '#4f7d87',
                   'icon_code': 'music-note-beamed',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Music',
                   'sort_order': 13,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751894',
                   'updated_at': None,
                   'description': 'Bandcamp is an artist-friendly marketplace where musicians sell '
                                  'albums, merch, and direct-to-fan subscriptions while keeping '
                                  'most of the revenue. Linking your page lets supporters buy '
                                  'records, download discographies, and follow tour updates from '
                                  'one fan-funded storefront.',
                   'home_url': 'https://bandcamp.com/'},
                  {'id': 15,
                   'name': 'X / Twitter',
                   'domains': 'twitter.com,x.com',
                   'url': 'https://twitter.com/',
                   'profile_pattern': 'https://twitter.com/{handle}',
                   'bg_color': '#000000',
                   'text_color': '#ffffff',
                   'border_color': '#333333',
                   'icon_code': 'twitter-x',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Social',
                   'sort_order': 14,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751895',
                   'updated_at': None,
                   'description': 'X, formerly Twitter, is the real-time public square for news, '
                                  'commentary, and niche communities built around short posts and '
                                  'reposts. Adding your handle lets visitors follow your takes, '
                                  'threads, and live event coverage wherever the conversation '
                                  'happens first.',
                   'home_url': 'https://twitter.com/'},
                  {'id': 16,
                   'name': 'LinkedIn',
                   'domains': 'linkedin.com',
                   'url': 'https://linkedin.com/',
                   'profile_pattern': 'https://linkedin.com/{handle}',
                   'bg_color': '#0a66c2',
                   'text_color': '#ffffff',
                   'border_color': '#085299',
                   'icon_code': 'linkedin',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Social',
                   'sort_order': 15,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751896',
                   'updated_at': None,
                   'description': 'LinkedIn is the professional network where careers, companies, '
                                  'and industry insights live side by side through profiles, '
                                  'endorsements, and articles. Link yours so recruiters, clients, '
                                  'and colleagues can verify your experience, recommendations, and '
                                  'current role in one click.',
                   'home_url': 'https://linkedin.com/'},
                  {'id': 17,
                   'name': 'Reddit',
                   'domains': 'reddit.com',
                   'url': 'https://reddit.com/',
                   'profile_pattern': 'https://reddit.com/{handle}',
                   'bg_color': '#ff4500',
                   'text_color': '#ffffff',
                   'border_color': '#d63a00',
                   'icon_code': 'reddit',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Social',
                   'sort_order': 16,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751898',
                   'updated_at': None,
                   'description': 'Reddit is a vast network of topic-focused communities where '
                                  'members vote, discuss, and moderate discussions ranging from '
                                  'hobbies to research. Linking your account shows your post '
                                  'history, awards, and favourite subreddits, proving active '
                                  'participation in the communities you care about.',
                   'home_url': 'https://reddit.com/'},
                  {'id': 18,
                   'name': 'Quora',
                   'domains': 'quora.com',
                   'url': 'https://quora.com/',
                   'profile_pattern': 'https://quora.com/{handle}',
                   'bg_color': '#b92b27',
                   'text_color': '#ffffff',
                   'border_color': '#97231f',
                   'icon_code': 'question-circle',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Social',
                   'sort_order': 17,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751899',
                   'updated_at': None,
                   'description': 'Quora is a question-and-answer knowledge network where experts '
                                  'and enthusiasts write long-form answers that keep surfacing in '
                                  'search results. Sharing your profile highlights the topics you '
                                  'explain best, your answer view counts, and any credentials you '
                                  'have verified.',
                   'home_url': 'https://quora.com/'},
                  {'id': 19,
                   'name': 'Facebook',
                   'domains': 'facebook.com',
                   'url': 'https://facebook.com/',
                   'profile_pattern': 'https://facebook.com/{handle}',
                   'bg_color': '#1877f2',
                   'text_color': '#ffffff',
                   'border_color': '#125ecc',
                   'icon_code': 'facebook',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Social',
                   'sort_order': 18,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751900',
                   'updated_at': None,
                   'description': 'Facebook remains one of the largest social networks for '
                                  'personal pages, interest Groups, local events, and business '
                                  'presence. Linking your public page or profile helps family, '
                                  'customers, and community members find the place where you '
                                  'actually post and organise.',
                   'home_url': 'https://facebook.com/'},
                  {'id': 20,
                   'name': 'Instagram',
                   'domains': 'instagram.com',
                   'url': 'https://instagram.com/',
                   'profile_pattern': 'https://instagram.com/{handle}',
                   'bg_color': '#c13584',
                   'text_color': '#ffffff',
                   'border_color': '#9a2a69',
                   'icon_code': 'instagram',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Social',
                   'sort_order': 19,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751901',
                   'updated_at': None,
                   'description': 'Instagram is a photo and short-video network built around '
                                  'feeds, Stories, Reels, and creator commerce tools. Linking your '
                                  'handle gives visitors instant access to your visual portfolio, '
                                  'follower community, and behind-the-scenes updates from wherever '
                                  'you are shooting.',
                   'home_url': 'https://instagram.com/'},
                  {'id': 21,
                   'name': 'TikTok',
                   'domains': 'tiktok.com',
                   'url': 'https://tiktok.com/',
                   'profile_pattern': 'https://tiktok.com/{handle}',
                   'bg_color': '#010101',
                   'text_color': '#ffffff',
                   'border_color': '#25f4ee',
                   'icon_code': 'camera-reels',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Social',
                   'sort_order': 20,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751902',
                   'updated_at': None,
                   'description': 'TikTok is the vertical short-video platform whose '
                                  'recommendation engine can turn unknown creators into global '
                                  'names overnight. Add your profile so fans can watch your latest '
                                  'clips, duets, and trending sounds, and follow the series that '
                                  'grow your audience week over week.',
                   'home_url': 'https://tiktok.com/'},
                  {'id': 22,
                   'name': 'Threads',
                   'domains': 'threads.net',
                   'url': 'https://threads.net/',
                   'profile_pattern': 'https://threads.net/{handle}',
                   'bg_color': '#000000',
                   'text_color': '#ffffff',
                   'border_color': '#333333',
                   'icon_code': 'at',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Social',
                   'sort_order': 21,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751915',
                   'updated_at': None,
                   'description': "Threads is Meta's text-forward social app, tightly connected to "
                                  'Instagram and designed for quick replies, conversations, and '
                                  'public updates. Linking it lets readers continue discussions '
                                  'started elsewhere and follow your day-to-day writing in a '
                                  'calmer, chronological feed.',
                   'home_url': 'https://threads.net/'},
                  {'id': 23,
                   'name': 'Patreon',
                   'domains': 'patreon.com',
                   'url': 'https://patreon.com/',
                   'profile_pattern': 'https://patreon.com/{handle}',
                   'bg_color': '#f96854',
                   'text_color': '#ffffff',
                   'border_color': '#c94f3f',
                   'icon_code': 'heart-fill',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Store',
                   'sort_order': 22,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751916',
                   'updated_at': None,
                   'description': 'Patreon powers membership businesses: creators offer tiered '
                                  'subscriptions with bonus episodes, early access, and community '
                                  'perks. Linking your campaign shows how many patrons back your '
                                  'work and gives supporters the shortest possible path to a '
                                  'recurring contribution.',
                   'home_url': 'https://patreon.com/'},
                  {'id': 24,
                   'name': 'Ko-fi',
                   'domains': 'ko-fi.com',
                   'url': 'https://ko-fi.com/',
                   'profile_pattern': 'https://ko-fi.com/{handle}',
                   'bg_color': '#ff5e5b',
                   'text_color': '#ffffff',
                   'border_color': '#d94c49',
                   'icon_code': 'cup-hot',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Store',
                   'sort_order': 23,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751918',
                   'updated_at': None,
                   'description': 'Ko-fi lets artists, developers, and writers accept one-off '
                                  'tips, run small shops, and offer memberships without taking a '
                                  'cut of donations. Your Ko-fi page is a low-friction way for '
                                  'appreciative visitors to buy you a coffee and support ongoing '
                                  'projects.',
                   'home_url': 'https://ko-fi.com/'},
                  {'id': 25,
                   'name': 'Buy Me a Coffee',
                   'domains': 'buymeacoffee.com',
                   'url': 'https://buymeacoffee.com/',
                   'profile_pattern': 'https://buymeacoffee.com/{handle}',
                   'bg_color': '#ffd43b',
                   'text_color': '#2b2b2b',
                   'border_color': '#e0b92f',
                   'icon_code': 'cash-coin',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Store',
                   'sort_order': 24,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751919',
                   'updated_at': None,
                   'description': 'Buy Me a Coffee provides a simple support button for creators, '
                                  'adding memberships, digital products, and comment widgets to '
                                  'any site. Linking your page turns casual readers into paying '
                                  'supporters with minimal friction and no complex store setup '
                                  'required.',
                   'home_url': 'https://buymeacoffee.com/'},
                  {'id': 26,
                   'name': 'Etsy',
                   'domains': 'etsy.com',
                   'url': 'https://etsy.com/',
                   'profile_pattern': 'https://etsy.com/{handle}',
                   'bg_color': '#f16521',
                   'text_color': '#ffffff',
                   'border_color': '#c95119',
                   'icon_code': 'shop',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Store',
                   'sort_order': 25,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751920',
                   'updated_at': None,
                   'description': 'Etsy is the global marketplace for handmade goods, vintage '
                                  'finds, and craft supplies, run largely by independent studio '
                                  'sellers. Linking your shop lets buyers browse listings, check '
                                  'reviews, and favourite your products without leaving the '
                                  'profile you are already viewing.',
                   'home_url': 'https://etsy.com/'},
                  {'id': 27,
                   'name': 'Amazon Storefront',
                   'domains': 'amazon.com,amzn.to',
                   'url': 'https://amazon.com/',
                   'profile_pattern': 'https://amazon.com/{handle}',
                   'bg_color': '#ff9900',
                   'text_color': '#131921',
                   'border_color': '#e08700',
                   'icon_code': 'bag',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Store',
                   'sort_order': 26,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751922',
                   'updated_at': None,
                   'description': 'An Amazon Storefront curates the products a creator or business '
                                  'recommends into shoppable lists, wishlists, and brand pages. '
                                  'Linking yours lets visitors buy the gear you actually use while '
                                  'giving you affiliate credit for the referrals your content '
                                  'generates.',
                   'home_url': 'https://amazon.com/'},
                  {'id': 28,
                   'name': 'Personal Website',
                   'domains': '',
                   'url': None,
                   'profile_pattern': None,
                   'bg_color': '#f8f9fa',
                   'text_color': '#212529',
                   'border_color': '#dee2e6',
                   'icon_code': 'globe2',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Portfolio',
                   'sort_order': 27,
                   'is_active': 1,
                   'is_adult': 0,
                   'is_deleted': 0,
                   'created_at': '2026-09-29 23:53:23.751923',
                   'updated_at': None,
                   'description': 'Your own domain is the one profile you fully control: a '
                                  'personal website hosts your portfolio, writing, case studies, '
                                  'and contact details without algorithm interference. Link it as '
                                  'the anchor of your footprint so every network points back to '
                                  'your home base.',
                   'home_url': None},
                  {'id': 29,
                   'name': 'Demo Adult Network',
                   'domains': 'demo-adult.example',
                   'url': 'https://demo-adult.example',
                   'profile_pattern': 'https://demo-adult.example/{handle}',
                   'bg_color': '#4d0a0a',
                   'text_color': '#ffffff',
                   'border_color': '#2f0606',
                   'icon_code': 'shield-lock',
                   'custom_icon_path': None,
                   'is_nofollow': 0,
                   'cta': None,
                   'category': 'Other',
                   'sort_order': 0,
                   'is_active': 1,
                   'is_adult': 1,
                   'is_deleted': 0,
                   'created_at': '2026-10-07 18:54:56.712530',
                   'updated_at': None,
                   'description': None,
                   'home_url': 'https://demo-adult.example/'}],
 'faq_categories': [{'id': 1,
                     'name': 'Getting Started',
                     'slug': 'getting-started',
                     'description': 'Set up your profile and share your link.',
                     'sort_order': 0,
                     'is_active': 1,
                     'created_at': '2026-10-13 03:16:08.970377'},
                    {'id': 2,
                     'name': 'Plans & Billing',
                     'slug': 'plans-billing',
                     'description': 'Plans, limits, and custom domains.',
                     'sort_order': 1,
                     'is_active': 1,
                     'created_at': '2026-10-13 03:16:08.971804'},
                    {'id': 3,
                     'name': 'Search & AI Visibility',
                     'slug': 'search-ai-visibility',
                     'description': 'How profiles are discovered by search engines and AI '
                                    'assistants.',
                     'sort_order': 2,
                     'is_active': 1,
                     'created_at': '2026-10-13 03:16:08.972602'},
                    {'id': 4,
                     'name': 'Links & Analytics',
                     'slug': 'links-analytics',
                     'description': 'Managing links, tracking, and link health.',
                     'sort_order': 3,
                     'is_active': 1,
                     'created_at': '2026-10-13 03:16:08.973161'}],
 'content_sections': [{'id': 1,
                       'name': 'Home page testimonials',
                       'slug': 'homepage-testimonials',
                       'description': 'The home page social-proof area.',
                       'sort_order': 0,
                       'is_active': 1},
                      {'id': 2,
                       'name': 'Home page features',
                       'slug': 'homepage-features',
                       'description': 'The features section on the home page.',
                       'sort_order': 1,
                       'is_active': 1},
                      {'id': 3,
                       'name': 'Home page interactive demo',
                       'slug': 'homepage-demo',
                       'description': 'The interactive demo section on the home page.',
                       'sort_order': 2,
                       'is_active': 1},
                      {'id': 4,
                       'name': 'Home page pricing',
                       'slug': 'homepage-pricing',
                       'description': 'The pricing section on the home page.',
                       'sort_order': 3,
                       'is_active': 1},
                      {'id': 5,
                       'name': 'Home page FAQ',
                       'slug': 'homepage-faq',
                       'description': 'The frequently asked questions section on the home page.',
                       'sort_order': 4,
                       'is_active': 1},
                      {'id': 6,
                       'name': 'Home page final call to action',
                       'slug': 'homepage-final-cta',
                       'description': 'The closing call-to-action section on the home page.',
                       'sort_order': 5,
                       'is_active': 1}],
 'faqs': [{'id': 1,
           'question': 'Can I use my own domain name?',
           'answer': "Yes — custom domains are available on the Custom plan. Point your domain's "
                     "DNS at us and we'll serve your profile page with SSL automatically. On lower "
                     'plans you get a clean canonical URL like /yourname.',
           'category_id': 2,
           'sort_order': 0,
           'is_published': 1,
           'created_at': '2026-10-13 03:16:08.974946',
           'updated_at': None},
          {'id': 2,
           'question': 'How do I add my Google Analytics or Meta tracking pixel?',
           'answer': 'On the Full and Custom plans, open the Dashboard → Integrations section, '
                     'paste your GA4 Measurement ID, Google Tag Manager container, or Meta Pixel '
                     'ID, and save. Pixels load only on your public profile pages and never track '
                     'logged-in editing sessions.',
           'category_id': 4,
           'sort_order': 1,
           'is_published': 1,
           'created_at': '2026-10-13 03:16:08.974949',
           'updated_at': None},
          {'id': 3,
           'question': 'Is there a limit on how many links I can add on paid tiers?',
           'answer': 'The Free plan includes {free_max} active links and Expanded includes '
                     '{expanded_max}. The Full and Custom plans are unlimited — publish as many '
                     'links, embeds, and sections as you like. Hidden (inactive) links never count '
                     'against your limit.',
           'category_id': 2,
           'sort_order': 2,
           'is_published': 1,
           'created_at': '2026-10-13 03:16:08.974950',
           'updated_at': None},
          {'id': 4,
           'question': 'How does the platform help my profile show up in Google search results?',
           'answer': 'Every public profile ships with semantic HTML, Open Graph meta tags, and '
                     'JSON-LD schema.org Person markup generated from your display name, headline, '
                     'bio, and links. That means richer search snippets — and AI assistants like '
                     'ChatGPT and Perplexity can accurately cite who you are and where you '
                     'publish.',
           'category_id': 3,
           'sort_order': 3,
           'is_published': 1,
           'created_at': '2026-10-13 03:16:08.974951',
           'updated_at': None},
          {'id': 5,
           'question': 'What happens if one of my links breaks?',
           'answer': 'Our automated link checker visits your links on a schedule. If a destination '
                     'returns a 404 or errors out, you receive a heads-up in your inbox (throttled '
                     'so you never get spammed about the same issue) so you can fix or hide it '
                     'fast.',
           'category_id': 4,
           'sort_order': 4,
           'is_published': 1,
           'created_at': '2026-10-13 03:16:08.974952',
           'updated_at': None}],
 'faq_sections': [{'faq_id': 1, 'content_section_id': 5},
                  {'faq_id': 2, 'content_section_id': 5},
                  {'faq_id': 3, 'content_section_id': 5},
                  {'faq_id': 4, 'content_section_id': 5},
                  {'faq_id': 5, 'content_section_id': 5}]}


def _seed_catalogue():
    from datetime import datetime
    conn = op.get_bind()
    for name, rows in SEED_DATA.items():
        table = sa.Table(name, sa.MetaData(), autoload_with=conn)
        prepared = []
        for row in rows:
            row = dict(row)
            for col in table.columns:
                val = row.get(col.name)
                if isinstance(val, str) and isinstance(col.type, sa.DateTime):
                    row[col.name] = datetime.fromisoformat(val)
            prepared.append(row)
        if prepared:
            conn.execute(table.insert(), prepared)


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('content_sections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=250), nullable=True),
    sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='1', nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    with op.batch_alter_table('content_sections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_content_sections_slug'), ['slug'], unique=True)

    op.create_table('faq_categories',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=250), nullable=True),
    sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    with op.batch_alter_table('faq_categories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_faq_categories_slug'), ['slug'], unique=True)

    op.create_table('link_categories',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('icon_code', sa.String(length=50), nullable=False),
    sa.Column('bg_color', sa.String(length=9), nullable=False),
    sa.Column('text_color', sa.String(length=9), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('is_adult', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('is_deleted', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('link_sources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('domains', sa.String(length=300), nullable=True),
    sa.Column('url', sa.String(length=300), nullable=True),
    sa.Column('home_url', sa.String(length=300), nullable=True),
    sa.Column('profile_pattern', sa.String(length=300), nullable=True),
    sa.Column('bg_color', sa.String(length=9), nullable=False),
    sa.Column('text_color', sa.String(length=9), nullable=False),
    sa.Column('border_color', sa.String(length=9), nullable=False),
    sa.Column('icon_code', sa.String(length=50), nullable=True),
    sa.Column('custom_icon_path', sa.String(length=300), nullable=True),
    sa.Column('is_nofollow', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('cta', sa.String(length=40), nullable=True),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.Column('category', sa.String(length=40), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('is_adult', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('is_deleted', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('reply_macros',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('review_invite_templates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('saved_views',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('page', sa.String(length=20), nullable=False),
    sa.Column('query_string', sa.String(length=300), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.String(length=500), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('triage_rules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('match_reason', sa.String(length=30), nullable=True),
    sa.Column('match_domain', sa.String(length=120), nullable=True),
    sa.Column('match_keyword', sa.String(length=80), nullable=True),
    sa.Column('set_status', sa.String(length=20), nullable=False),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default='1', nullable=False),
    sa.Column('priority', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=80), nullable=False),
    sa.Column('email', sa.String(length=120), nullable=False),
    sa.Column('password_hash', sa.String(length=256), nullable=False),
    sa.Column('display_name', sa.String(length=120), nullable=True),
    sa.Column('headline', sa.String(length=250), nullable=True),
    sa.Column('bio', sa.Text(), nullable=True),
    sa.Column('about_section', sa.Text(), nullable=True),
    sa.Column('avatar_url', sa.String(length=500), nullable=True),
    sa.Column('avatar_path', sa.String(length=300), nullable=True),
    sa.Column('background_path', sa.String(length=300), nullable=True),
    sa.Column('tier', sa.String(length=20), nullable=True),
    sa.Column('tier_changed_at', sa.DateTime(), nullable=True),
    sa.Column('stripe_customer_id', sa.String(length=64), nullable=True),
    sa.Column('stripe_subscription_id', sa.String(length=64), nullable=True),
    sa.Column('stripe_subscription_status', sa.String(length=32), nullable=True),
    sa.Column('stripe_current_period_end', sa.BigInteger(), nullable=True),
    sa.Column('skills', sa.String(length=300), nullable=True),
    sa.Column('location', sa.String(length=120), nullable=True),
    sa.Column('country_code', sa.String(length=3), nullable=True),
    sa.Column('country_name', sa.String(length=120), nullable=True),
    sa.Column('is_adult_oriented', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('video_platform', sa.String(length=10), nullable=True),
    sa.Column('video_id', sa.String(length=64), nullable=True),
    sa.Column('ga4_id', sa.String(length=32), nullable=True),
    sa.Column('is_admin', sa.Boolean(), nullable=False),
    sa.Column('theme', sa.String(length=20), nullable=True),
    sa.Column('contact_disabled', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('directory_visible', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('age_verified', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('is_featured', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('is_demo', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_country_code'), ['country_code'], unique=False)
        batch_op.create_index(batch_op.f('ix_users_display_name'), ['display_name'], unique=True)
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)
        batch_op.create_index(batch_op.f('ix_users_stripe_customer_id'), ['stripe_customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_users_username'), ['username'], unique=True)

    op.create_table('activity',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('detail', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('activity', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_activity_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_activity_kind'), ['kind'], unique=False)

    op.create_table('contact_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('recipient_id', sa.Integer(), nullable=False),
    sa.Column('sender_name', sa.String(length=80), nullable=False),
    sa.Column('sender_email', sa.String(length=120), nullable=False),
    sa.Column('subject', sa.String(length=120), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('sender_ip', sa.String(length=45), nullable=True),
    sa.Column('is_spam', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('is_read', sa.Boolean(), nullable=False),
    sa.Column('is_archived', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.ForeignKeyConstraint(['recipient_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('contact_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contact_messages_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_contact_messages_recipient_id'), ['recipient_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_contact_messages_status'), ['status'], unique=False)

    op.create_table('faqs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('question', sa.String(length=300), nullable=False),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('category_id', sa.Integer(), nullable=True),
    sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
    sa.Column('is_published', sa.Boolean(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['category_id'], ['faq_categories.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('faqs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_faqs_category_id'), ['category_id'], unique=False)

    op.create_table('links',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('url', sa.String(length=500), nullable=False),
    sa.Column('icon_code', sa.String(length=50), nullable=True),
    sa.Column('position', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('click_count', sa.Integer(), nullable=True),
    sa.Column('source_id', sa.Integer(), nullable=True),
    sa.Column('category', sa.String(length=40), nullable=True),
    sa.Column('is_pinned', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('utm_params', sa.String(length=300), nullable=True),
    sa.Column('subhandle', sa.String(length=80), nullable=True),
    sa.Column('cta', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['source_id'], ['link_sources.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('category', sa.String(length=30), nullable=False),
    sa.Column('severity', sa.String(length=20), nullable=False),
    sa.Column('action_url', sa.String(length=500), nullable=True),
    sa.Column('action_label', sa.String(length=60), nullable=True),
    sa.Column('sender_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('dedupe_key', sa.String(length=120), nullable=True),
    sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_messages_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_messages_dedupe_key'), ['dedupe_key'], unique=False)

    op.create_table('testimonials',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('author_name', sa.String(length=80), nullable=False),
    sa.Column('author_role', sa.String(length=120), nullable=True),
    sa.Column('quote', sa.Text(), nullable=False),
    sa.Column('rating', sa.Integer(), server_default='5', nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('profile_consent', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('is_published', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('exclude_from_home', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('is_incomplete', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('resolution_status', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('is_flagged', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('admin_note', sa.Text(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.Column('replied_at', sa.DateTime(), nullable=True),
    sa.Column('replied_by_id', sa.Integer(), nullable=True),
    sa.Column('invitation_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['invitation_id'], ['review_invitations.id'], name='fk_testimonials_invitation_id', use_alter=True),
    sa.ForeignKeyConstraint(['replied_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('testimonials', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_testimonials_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_testimonials_invitation_id'), ['invitation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_testimonials_resolution_status'), ['resolution_status'], unique=False)
        batch_op.create_index(batch_op.f('ix_testimonials_user_id'), ['user_id'], unique=False)

    op.create_table('abuse_reports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('reporter_name', sa.String(length=80), nullable=True),
    sa.Column('reporter_email', sa.String(length=120), nullable=True),
    sa.Column('target_user_id', sa.Integer(), nullable=False),
    sa.Column('target_link_id', sa.Integer(), nullable=True),
    sa.Column('reason', sa.String(length=30), nullable=False),
    sa.Column('details', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('admin_note', sa.Text(), nullable=True),
    sa.Column('resolved_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['resolved_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['target_link_id'], ['links.id'], ),
    sa.ForeignKeyConstraint(['target_user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('abuse_reports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_abuse_reports_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_abuse_reports_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_abuse_reports_target_user_id'), ['target_user_id'], unique=False)

    op.create_table('faq_sections',
    sa.Column('faq_id', sa.Integer(), nullable=False),
    sa.Column('content_section_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['content_section_id'], ['content_sections.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['faq_id'], ['faqs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('faq_id', 'content_section_id')
    )
    op.create_table('link_check_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('link_id', sa.Integer(), nullable=False),
    sa.Column('http_status', sa.Integer(), nullable=True),
    sa.Column('is_broken', sa.Boolean(), nullable=False),
    sa.Column('checked_at', sa.DateTime(), nullable=True),
    sa.Column('broken_since', sa.DateTime(), nullable=True),
    sa.Column('last_notified_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['link_id'], ['links.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('link_check_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_link_check_results_is_broken'), ['is_broken'], unique=False)
        batch_op.create_index(batch_op.f('ix_link_check_results_link_id'), ['link_id'], unique=True)

    op.create_table('message_reads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('is_read', sa.Boolean(), nullable=False),
    sa.Column('is_archived', sa.Boolean(), nullable=False),
    sa.Column('delivered_at', sa.DateTime(), nullable=True),
    sa.Column('read_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('message_id', 'user_id', name='uq_message_user')
    )
    with op.batch_alter_table('message_reads', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_message_reads_message_id'), ['message_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_message_reads_user_id'), ['user_id'], unique=False)

    op.create_table('review_invitations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('batch_id', sa.String(length=32), nullable=True),
    sa.Column('message_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('expires_days', sa.Integer(), server_default='30', nullable=False),
    sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('submitted_at', sa.DateTime(), nullable=True),
    sa.Column('testimonial_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ),
    sa.ForeignKeyConstraint(['testimonial_id'], ['testimonials.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('review_invitations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_review_invitations_batch_id'), ['batch_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_review_invitations_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_review_invitations_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_review_invitations_token'), ['token'], unique=True)
        batch_op.create_index(batch_op.f('ix_review_invitations_user_id'), ['user_id'], unique=False)

    op.create_table('testimonial_replies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('testimonial_id', sa.Integer(), nullable=False),
    sa.Column('admin_user_id', sa.Integer(), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('dispatched_at', sa.DateTime(), nullable=True),
    sa.Column('resolution_status', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['admin_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['testimonial_id'], ['testimonials.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('testimonial_replies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_testimonial_replies_admin_user_id'), ['admin_user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_testimonial_replies_dispatched_at'), ['dispatched_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_testimonial_replies_resolution_status'), ['resolution_status'], unique=False)
        batch_op.create_index(batch_op.f('ix_testimonial_replies_testimonial_id'), ['testimonial_id'], unique=False)

    op.create_table('testimonial_sections',
    sa.Column('testimonial_id', sa.Integer(), nullable=False),
    sa.Column('content_section_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['content_section_id'], ['content_sections.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['testimonial_id'], ['testimonials.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('testimonial_id', 'content_section_id')
    )
    # ### end Alembic commands ###
    _seed_catalogue()


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('testimonial_sections')
    with op.batch_alter_table('testimonial_replies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_testimonial_replies_testimonial_id'))
        batch_op.drop_index(batch_op.f('ix_testimonial_replies_resolution_status'))
        batch_op.drop_index(batch_op.f('ix_testimonial_replies_dispatched_at'))
        batch_op.drop_index(batch_op.f('ix_testimonial_replies_admin_user_id'))

    op.drop_table('testimonial_replies')
    with op.batch_alter_table('review_invitations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_review_invitations_user_id'))
        batch_op.drop_index(batch_op.f('ix_review_invitations_token'))
        batch_op.drop_index(batch_op.f('ix_review_invitations_status'))
        batch_op.drop_index(batch_op.f('ix_review_invitations_created_at'))
        batch_op.drop_index(batch_op.f('ix_review_invitations_batch_id'))

    op.drop_table('review_invitations')
    with op.batch_alter_table('message_reads', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_message_reads_user_id'))
        batch_op.drop_index(batch_op.f('ix_message_reads_message_id'))

    op.drop_table('message_reads')
    with op.batch_alter_table('link_check_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_link_check_results_link_id'))
        batch_op.drop_index(batch_op.f('ix_link_check_results_is_broken'))

    op.drop_table('link_check_results')
    op.drop_table('faq_sections')
    with op.batch_alter_table('abuse_reports', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_abuse_reports_target_user_id'))
        batch_op.drop_index(batch_op.f('ix_abuse_reports_status'))
        batch_op.drop_index(batch_op.f('ix_abuse_reports_created_at'))

    op.drop_table('abuse_reports')
    with op.batch_alter_table('testimonials', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_testimonials_user_id'))
        batch_op.drop_index(batch_op.f('ix_testimonials_resolution_status'))
        batch_op.drop_index(batch_op.f('ix_testimonials_invitation_id'))
        batch_op.drop_index(batch_op.f('ix_testimonials_created_at'))

    op.drop_table('testimonials')
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_messages_dedupe_key'))
        batch_op.drop_index(batch_op.f('ix_messages_created_at'))

    op.drop_table('messages')
    op.drop_table('links')
    with op.batch_alter_table('faqs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_faqs_category_id'))

    op.drop_table('faqs')
    with op.batch_alter_table('contact_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contact_messages_status'))
        batch_op.drop_index(batch_op.f('ix_contact_messages_recipient_id'))
        batch_op.drop_index(batch_op.f('ix_contact_messages_created_at'))

    op.drop_table('contact_messages')
    with op.batch_alter_table('activity', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_activity_kind'))
        batch_op.drop_index(batch_op.f('ix_activity_created_at'))

    op.drop_table('activity')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_username'))
        batch_op.drop_index(batch_op.f('ix_users_stripe_customer_id'))
        batch_op.drop_index(batch_op.f('ix_users_email'))
        batch_op.drop_index(batch_op.f('ix_users_display_name'))
        batch_op.drop_index(batch_op.f('ix_users_country_code'))

    op.drop_table('users')
    op.drop_table('triage_rules')
    op.drop_table('settings')
    op.drop_table('saved_views')
    op.drop_table('review_invite_templates')
    op.drop_table('reply_macros')
    op.drop_table('link_sources')
    op.drop_table('link_categories')
    with op.batch_alter_table('faq_categories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_faq_categories_slug'))

    op.drop_table('faq_categories')
    with op.batch_alter_table('content_sections', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_content_sections_slug'))

    op.drop_table('content_sections')
    # ### end Alembic commands ###
