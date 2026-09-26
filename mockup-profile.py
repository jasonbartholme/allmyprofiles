# 1. Update User Profile Data Structure with Skills, Adult Flag, Intro Video, and GA4 ID
user_profile = {
    "avatar_url": "https://ui-avatars.com/api/?name=Jane+Doe&background=0D8ABC&color=fff&size=128",
    "display_name": "Jane Doe",
    "handle": "@janedoe",
    "bio": "Software Engineer | Content Creator | Coffee Enthusiast",
    "location": "San Francisco, CA",
    "bg_color": "#f4f6f8",
    "bg_image_url": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?q=80&w=500&auto=format&fit=crop",
    "text_color": "#333333",
    "skills": ["Python", "Flask", "UI/UX", "Gaming"], # NEW: Skill Badges
    "is_adult_oriented": True, # NEW: Adult profile flag
    "intro_video": { # NEW: Intro Video
        "platform": "youtube", # Options: 'youtube' or 'vimeo'
        "video_id": "dQw4w9WgXcQ" # Example YouTube ID
    },
    "ga4_id": "G-ABC123XYZ9" # NEW: User's personal Google Analytics 4 ID
}

# 2. Enrich Social Links (Mocking Categories, Pinned status, and optional UTMs)
for link in social_links:
    # Assign Mock Categories based on name
    if link['name'] in ['GitHub', 'GitLab', 'Steam', 'Xbox Live', 'PlayStation Network']:
        link['category'] = 'Gaming & Dev'
    elif link['name'] in ['YouTube', 'Twitch', 'Vimeo', 'Medium', 'Spotify']:
        link['category'] = 'Content'
    elif link['name'] in ['Twitter / X', 'LinkedIn', 'Reddit', 'Quora']:
        link['category'] = 'Social'
    else:
        link['category'] = 'Other'

    # Mock Pin (Let's pin GitHub to show the primary property)
    link['is_pinned'] = (link['name'] == 'GitHub')

    # Mock Optional UTM Code (added to the pinned link as an example)
    if link['is_pinned']:
        link['utm_params'] = "?utm_source=my_profile_page&utm_medium=referral"
    else:
        link['utm_params'] = "" # Keep it clean if no UTM is provided

# Group links for Jinja processing
pinned_links = [l for l in social_links if l.get('is_pinned')]
categorized_links = {}
for l in social_links:
    if not l.get('is_pinned'):
        categorized_links.setdefault(l['category'], []).append(l)

# 3. Updated Jinja2 Template combining Profile Header + Intro Video + Social Links + Tabs + Modal + Footer + GA4
combined_template_string = """
<!-- Include Bootstrap CSS, JS & Icons -->
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css">

{% if profile.ga4_id %}
<!-- Google tag (gtag.js) - User's Custom GA4 Tracking -->
<script async src="https://www.googletagmanager.com/gtag/js?id={{ profile.ga4_id }}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());

  gtag('config', '{{ profile.ga4_id }}');
</script>
{% endif %}

<main class="container mt-5 mb-2" style="max-width: 500px;" aria-label="{{ profile.display_name }}'s Linktree Profile">

    <!-- === PROFILE HEADER CARD === -->
    <section class="card shadow-sm mb-4 border-0"
         style="background-color: {{ profile.bg_color }};
                {% if profile.bg_image_url %}
                background-image: url('{{ profile.bg_image_url }}');
                background-size: cover;
                background-position: center;
                {% endif %}
                border-radius: 20px;
                position: relative; overflow: hidden;"
         aria-labelledby="profile-name">

        {% if profile.is_adult_oriented %}
        <!-- Adult Content Ribbon -->
        <div aria-label="18+ Adult Content Warning" style="position: absolute; top: 0; right: 0; overflow: hidden; width: 120px; height: 120px; z-index: 10;">
            <div style="position: absolute; top: 22px; right: -32px; width: 140px; background-color: #dc3545; color: #fff; text-align: center; font-size: 0.75rem; font-weight: bold; padding: 4px 0; transform: rotate(45deg); box-shadow: 0 2px 4px rgba(0,0,0,0.3); letter-spacing: 1px;">
                18+ ADULT
            </div>
        </div>
        {% endif %}

        <div class="card-body text-center p-4">
            <!-- Avatar -->
            <img src="{{ profile.avatar_url }}" alt="Profile avatar of {{ profile.display_name }}"
                 class="rounded-circle mb-3 shadow-sm"
                 style="width: 110px; height: 110px; object-fit: cover; border: 4px solid #ffffff;">

            <!-- User Info -->
            <h3 id="profile-name" class="card-title fw-bold mb-0" style="color: {{ profile.text_color }};">
                {{ profile.display_name }}
            </h3>
            <p class="mb-3" style="color: {{ profile.text_color }}; opacity: 0.7;" aria-label="User Handle">
                {{ profile.handle }}
            </p>

            {% if profile.bio %}
            <p class="card-text mb-2" style="color: {{ profile.text_color }};">
                {{ profile.bio }}
            </p>
            {% endif %}

            <!-- Skill Badges -->
            {% if profile.skills %}
            <div class="d-flex flex-wrap justify-content-center gap-2 mb-3" aria-label="Skills">
                {% for skill in profile.skills %}
                <span class="badge rounded-pill" style="background-color: {{ profile.text_color }}; color: {{ profile.bg_color }}; opacity: 0.8;">
                    {{ skill }}
                </span>
                {% endfor %}
            </div>
            {% endif %}

            {% if profile.location %}
            <div class="d-flex justify-content-center align-items-center gap-1 mb-3" style="color: {{ profile.text_color }}; opacity: 0.8;" aria-label="Location: {{ profile.location }}">
                <i class="bi bi-geo-alt-fill" aria-hidden="true"></i>
                <small>{{ profile.location }}</small>
            </div>
            {% endif %}

            <!-- Contact Modal Trigger -->
            <button type="button" class="btn btn-sm px-4 rounded-pill fw-bold"
                    style="background-color: {{ profile.text_color }}; color: {{ profile.bg_color }};"
                    data-bs-toggle="modal" data-bs-target="#contactModal"
                    aria-label="Contact {{ profile.display_name }}" aria-haspopup="dialog">
                <i class="bi bi-envelope-fill me-2" aria-hidden="true"></i>Contact Me
            </button>
        </div>
    </section>

    <!-- === INTRO VIDEO SECTION === -->
    {% if profile.intro_video %}
    <section class="card shadow-sm mb-4 border-0" style="border-radius: 20px; overflow: hidden; background-color: {{ profile.bg_color }};" aria-label="Introduction Video">
        <div class="card-body p-0">
            <div class="ratio ratio-16x9">
                {% if profile.intro_video.platform == 'youtube' %}
                <iframe src="https://www.youtube.com/embed/{{ profile.intro_video.video_id }}" title="{{ profile.display_name }}'s YouTube intro video" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
                {% elif profile.intro_video.platform == 'vimeo' %}
                <iframe src="https://player.vimeo.com/video/{{ profile.intro_video.video_id }}" title="{{ profile.display_name }}'s Vimeo intro video" frameborder="0" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe>
                {% endif %}
            </div>
        </div>
    </section>
    {% endif %}

    <!-- === PINNED LINKS SECTION === -->
    {% if pinned_links %}
    <section class="mb-4" aria-labelledby="pinned-links-heading">
        <h6 id="pinned-links-heading" class="text-uppercase text-muted fw-bold mb-3 ms-2" style="font-size: 0.85rem;">
            <i class="bi bi-pin-angle-fill me-2" aria-hidden="true"></i>Pinned
        </h6>
        <div class="d-flex flex-column gap-3">
            {% for link in pinned_links %}
            <article class="card shadow transition-all"
                 style="background-color: {{ link.bg_color }};
                        border: 3px solid #ffc107;
                        color: {{ link.text_color }};
                        border-radius: 15px;">
                <div class="card-body d-flex align-items-center justify-content-between">
                    <div class="d-flex align-items-center gap-3">
                        <i class="{{ link.icon_class }} fs-2" aria-hidden="true"></i>
                        <div class="d-flex flex-column">
                            <h5 class="card-title mb-0 fw-bold">{{ link.name }}</h5>
                            <small style="opacity: 0.85;">@{{ link.handle }}</small>
                        </div>
                    </div>
                    <a href="{{ link.url }}{{ link.utm_params }}" class="btn btn-sm px-3"
                       style="background-color: {{ link.text_color }}; color: {{ link.bg_color }}; border-radius: 20px; font-weight: 600;"
                       target="_blank" rel="noopener noreferrer" aria-label="{{ link.cta }} on {{ link.name }}">{{ link.cta }}</a>
                </div>
            </article>
            {% endfor %}
        </div>
    </section>
    {% endif %}

    <!-- === TABBED SOCIAL CATEGORIES === -->
    <nav aria-label="Social Links Categories">
        <ul class="nav nav-pills nav-fill mb-3 gap-2" id="pills-tab" role="tablist">
            {% for category, links in categorized_links.items() %}
            <li class="nav-item" role="presentation">
                <button class="nav-link {% if loop.first %}active{% endif %} rounded-pill fw-bold"
                        id="pills-{{ loop.index }}-tab"
                        data-bs-toggle="pill"
                        data-bs-target="#pills-{{ loop.index }}"
                        type="button"
                        role="tab"
                        aria-controls="pills-{{ loop.index }}"
                        aria-selected="{% if loop.first %}true{% else %}false{% endif %}"
                        style="border: 1px solid #dee2e6;">
                    {{ category }}
                </button>
            </li>
            {% endfor %}
        </ul>
    </nav>

    <div class="tab-content" id="pills-tabContent">
        {% for category, cat_links in categorized_links.items() %}
        <div class="tab-pane fade {% if loop.first %}show active{% endif %}"
             id="pills-{{ loop.index }}"
             role="tabpanel"
             aria-labelledby="pills-{{ loop.index }}-tab"
             tabindex="0">
            <div class="d-flex flex-column gap-3">
                {% for link in cat_links %}
                <article class="card shadow-sm transition-all"
                     style="background-color: {{ link.bg_color }};
                            border: 2px solid {{ link.border_color }};
                            color: {{ link.text_color }};
                            border-radius: 15px;">
                    <div class="card-body d-flex align-items-center justify-content-between">
                        <div class="d-flex align-items-center gap-3">
                            <i class="{{ link.icon_class }} fs-2" aria-hidden="true"></i>
                            <div class="d-flex flex-column">
                                <h5 class="card-title mb-0 fw-bold">{{ link.name }}</h5>
                                <small style="opacity: 0.85;">@{{ link.handle }}</small>
                            </div>
                        </div>
                        <a href="{{ link.url }}{{ link.utm_params }}" class="btn btn-sm px-3"
                           style="background-color: {{ link.text_color }}; color: {{ link.bg_color }}; border-radius: 20px; font-weight: 600;"
                           target="_blank" rel="noopener noreferrer" aria-label="{{ link.cta }} on {{ link.name }}">{{ link.cta }}</a>
                    </div>
                </article>
                {% endfor %}
            </div>
        </div>
        {% endfor %}
    </div>

    <!-- === FOOTER === -->
    <footer class="mt-5 mb-3 text-center" role="contentinfo">
        <div class="d-flex justify-content-center gap-3 mb-2" style="font-size: 0.85rem;">
            <a href="/terms" class="text-decoration-none fw-semibold" style="color: {{ profile.text_color }}; opacity: 0.7;" aria-label="Terms of Service">Terms</a>
            <a href="/privacy" class="text-decoration-none fw-semibold" style="color: {{ profile.text_color }}; opacity: 0.7;" aria-label="Privacy Policy">Privacy</a>
            <a href="/report" class="text-decoration-none fw-semibold text-danger" style="opacity: 0.8;" aria-label="Report Abuse">Report Abuse</a>
        </div>
        <small style="color: {{ profile.text_color }}; opacity: 0.5;">
            Powered by <strong>FlaskTree</strong>
        </small>
    </footer>

</main>

<!-- === CONTACT MODAL === -->
<div class="modal fade" id="contactModal" tabindex="-1" aria-labelledby="contactModalLabel" aria-hidden="true" role="dialog">
  <div class="modal-dialog modal-dialog-centered">
    <div class="modal-content" style="border-radius: 15px;">
      <div class="modal-header border-0 pb-0">
        <h5 class="modal-title fw-bold" id="contactModalLabel">Message {{ profile.display_name }}</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close modal"></button>
      </div>
      <div class="modal-body">
        <form action="/submit-contact" method="POST">
          <div class="mb-3">
            <label for="senderName" class="form-label fw-semibold">Your Name</label>
            <input type="text" class="form-control bg-light" id="senderName" name="name" required aria-required="true">
          </div>
          <div class="mb-3">
            <label for="senderEmail" class="form-label fw-semibold">Your Email</label>
            <input type="email" class="form-control bg-light" id="senderEmail" name="email" required aria-required="true">
          </div>
          <div class="mb-4">
            <label for="messageText" class="form-label fw-semibold">Message</label>
            <textarea class="form-control bg-light" id="messageText" name="message" rows="4" required aria-required="true"></textarea>
          </div>
          <button type="submit" class="btn w-100 rounded-pill fw-bold py-2"
                  style="background-color: {{ profile.text_color }}; color: {{ profile.bg_color }};">
            Send Message
          </button>
        </form>
      </div>
    </div>
  </div>
</div>
"""

# 4. Render and display the combined template
combined_jinja_template = Template(combined_template_string)
final_html_output = combined_jinja_template.render(
    profile=user_profile,
    pinned_links=pinned_links,
    categorized_links=categorized_links
)

display(HTML(final_html_output))
