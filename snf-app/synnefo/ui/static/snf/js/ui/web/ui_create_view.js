// Copyright 2011 GRNET S.A. All rights reserved.
// 
// Redistribution and use in source and binary forms, with or
// without modification, are permitted provided that the following
// conditions are met:
// 
//   1. Redistributions of source code must retain the above
//      copyright notice, this list of conditions and the following
//      disclaimer.
// 
//   2. Redistributions in binary form must reproduce the above
//      copyright notice, this list of conditions and the following
//      disclaimer in the documentation and/or other materials
//      provided with the distribution.
// 
// THIS SOFTWARE IS PROVIDED BY GRNET S.A. ``AS IS'' AND ANY EXPRESS
// OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
// WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
// PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL GRNET S.A OR
// CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
// SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
// LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
// USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
// AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
// LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
// ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
// POSSIBILITY OF SUCH DAMAGE.
// 
// The views and conclusions contained in the software and
// documentation are those of the authors and should not be
// interpreted as representing official policies, either expressed
// or implied, of GRNET S.A.
// 

;(function(root){

    // root
    var root = root;
    
    // setup namepsaces
    var snf = root.synnefo = root.synnefo || {};
    var models = snf.models = snf.models || {}
    var storage = snf.storage = snf.storage || {};
    var ui = snf.ui = snf.ui || {};
    var util = snf.util = snf.util || {};

    var views = snf.views = snf.views || {}

    // shortcuts
    var bb = root.Backbone;


    views.VMCreationPasswordView = views.Overlay.extend({
        view_id: "creation_password_view",
        content_selector: "#creation-password-overlay",
        css_class: 'overlay-password overlay-info',
        overlay_id: "creation-password-overlay",

        subtitle: "",
        title: "Machine password",

        initialize: function(options) {
            views.FeedbackView.__super__.initialize.apply(this, arguments);
            _.bindAll(this, 'show_password');

            this.password = this.$("#new-machine-password");
            this.copy = this.$(".clipboard");

            this.$(".show-machine").click(_.bind(function(){
                if (this.$(".show-machine").hasClass("in-progress")) {
                    return;
                }
                this.hide();
                snf.ui.main.show_vm_details(storage.vms.get(this.vm_id));
            }, this));

            _.bindAll(this, "handle_vm_added");
            storage.vms.bind("add", this.handle_vm_added);
            this.password.text("");
        },

        handle_vm_added: function() {
            this.$(".show-machine").removeClass("in-progress");
        },
        
        show_password: function() {
            this.$(".show-machine").addClass("in-progress");
            this.password.text(this.pass);
            if (storage.vms.get(this.vm_id)) {
                this.$(".show-machine").removeClass("in-progress");
            }
            
            this.clip = new snf.util.ClipHelper(this.copy, this.pass);
        },

        onClose: function() {
            this.password.text("");
            this.vm_id = undefined;
            try { delete this.clip; } catch (err) {};
        },
        
        beforeOpen: function() {
            this.copy.empty();
        },
        
        onOpen: function() {
            this.show_password();
        },

        show: function(pass, vm_id) {
            this.pass = pass;
            this.vm_id = vm_id;
            
            views.VMCreationPasswordView.__super__.show.apply(this, arguments);
        }
    })


    
    views.CreateVMStepView = views.View.extend({
        step: "1",
        title: "Image",
        submit: false,

        initialize: function(view) {
            this.parent = view;
            this.el = view.$("div.create-step-cont.step-" + this.step);
            this.header = this.$(".step-header .step-" + this.step);
            this.view_id = "create_step_" + this.step;

            views.CreateVMStepView.__super__.initialize.apply(this);
        },

        show: function() {
            // show current
            this.el.show();
            this.header.addClass("current");
            this.header.show();
            this.update_layout();
        },

        reset: function() {
        }
    })

    views.CreateImageSelectView = views.CreateVMStepView.extend({

        initialize: function() {
            views.CreateImageSelectView.__super__.initialize.apply(this, arguments);

            // elements
            this.images_list_cont = this.$(".images-list-cont");
            this.images_list = this.$(".images-list-cont ul");
            this.image_details = this.$(".images-info-cont");
            this.image_details_desc = this.$(".images-info-cont .description p");
            this.image_details_title = this.$(".images-info-cont h4");
            this.image_details_size = this.$(".images-info-cont .size p");
            this.image_details_os = this.$(".images-info-cont .os p");
            this.image_details_kernel = this.$(".images-info-cont .kernel p");
            this.image_details_gui = this.$(".images-info-cont .gui p");
            this.image_details_vm = this.$(".images-info-cont .vm-name p");

            this.types = this.$(".type-filter li");
            this.categories_list = this.$(".category-filters");
            
            // params initialization
            this.type_selections = ["system", "custom", "public"]
            this.selected_type = undefined;

            this.selected_categories = [];
            this.images = [];
            this.images_ids = [];
            this.custom_images = [];

            // handlers initialization
            this.init_handlers();
            this.init_position();
        },

        init_position: function() {
            //this.el.css({position: "absolute"});
            //this.el.css({top:"10px"})
        },
        
        init_handlers: function() {
            var self = this;
            this.types.live("click", function() {
                self.select_type($(this).attr("id").replace("type-select-",""));
            });

            this.$(".register-custom-image").live("click", function(){
                var confirm_close = true;
                if (confirm_close) {
                    snf.ui.main.custom_images_view.show(self.parent);
                } else {
                }
            })
        },

        update_images: function() {
            if (this.selected_type == "system") { 
                this.update_predefined_images();
            }
            if (this.selected_type == "custom") { 
                this.update_custom_images();
            }
            return this.images;
        },

        update_predefined_images: function() {
            this.images = storage.images.predefined();
            this.images_ids = _.map(this.images, function(img){return img.id});
        },

        update_custom_images: function() {
            if (this.selected_image && !storage.glance.images.get(this.selected_image.id)) {
                this.selected_image = undefined;
            }
            this.images = storage.glance.images.personal();
            this.images_ids = _.map(this.images, function(img){return img.id});
        },

        update_layout: function() {
            if (this.selected_type != "system") {
                this.select_type(this.selected_type);
            }
        },
        
        get_categories: function(images) {
            return [];
            return ["Desktop", "Server", "Linux", "Windows"];
        },

        reset_categories: function() {
            var categories = this.get_categories(this.images);
            this.categories_list.find("li").remove();

            _.each(categories, _.bind(function(cat) {
                var el = $("<li />");
                el.text(cat);
                this.categories_list.append(el);
            }, this));

            if (!categories.length) { 
                this.categories_list.parent().find(".clear").hide();
                this.categories_list.parent().find(".empty").show();
            } else {
                this.categories_list.parent().find(".clear").show();
                this.categories_list.parent().find(".empty").hide();
            }
        },
        
        select_type: function(type) {
            this.selected_image = undefined;
            this.selected_type = type;
            this.types.removeClass("selected");
            this.types.filter("#type-select-" + this.selected_type).addClass("selected");
            
            var check_load_date = new Date() - (this.last_custom_load || 0);
            
            if (type == "system") {
                this.storage = storage.images;
            } else if (type == "custom") {
                this.storage = storage.glance.images;
            }

            if (type == "custom" && check_load_date > 30000) {
                if (snf.storage.glance.images.length == 0) {
                    this.$(".images-list-cont .empty").hide();
                    this.$(".images-list-cont .loading").show();
                    this.$(".images-list-cont .images-list").hide();
                }

                this.show_list_loading();
                snf.storage.glance.images.fetch({refresh:true, success: _.bind(function(){
                    this.last_custom_load = new Date();
                    this.$(".images-list-cont .loading").hide();
                    this.reset_categories();
                    this.update_images();
                    this.reset_images();
                    this.$(".images-list-cont .images-list").show();
                    this.hide_list_loading();
                    this.selected_image = undefined;
                    this.select_image();
                }, this)});
                this.selected_image = undefined;
                this.select_image();
            } else {
                this.reset_categories();
                this.update_images();
                this.reset_images();
                this.hide_list_loading();
            }

            this.update_layout_for_type(type);
        },

        update_layout_for_type: function(type) {
            if (type == "custom") {
                this.$(".custom-action").show();
            } else {
                this.$(".custom-action").hide();
            }
        },

        show_list_loading: function() {
            this.$(".images-list-cont").addClass("loading");
        },

        hide_list_loading: function() {
            this.$(".images-list-cont").removeClass("loading");
        },

        select_image: function(image) {
            if (!image && this.images_ids.length) {
                if (this.selected_image && this.images_ids.indexOf(this.selected_image.id) > -1) {
                    image = this.selected_image;
                } else {
                    image = this.storage.get(this.images_ids[0]);
                }
            }
            
            if (!this.images_ids.length) { image = this.selected_image || undefined };
            
            if ((!this.selected_image && image) || (this.selected_image != image))
                this.trigger("change", image);

            this.selected_image = image;
                
            if (image) {
                this.image_details.show();
                this.images_list.find(".image-details").removeClass("selected");
                this.images_list.find(".image-details#create-vm-image-" + this.selected_image.id).addClass("selected");

                this.image_details_desc.hide().parent().hide();
                if (image.get("description"))
                    this.image_details_desc.text(image.get("description")).show().parent().show();
                
                var img = snf.ui.helpers.os_icon_tag(image.get("OS"))

                this.image_details_title.hide().parent().hide();
                if (image.get("name"))
                    this.image_details_title.html(img + image.get("name")).show().parent().show();

                this.image_details_os.hide().parent().hide();
                if (image.get("OS"))
                    this.image_details_os.text(_(image.get("OS")).capitalize()).show().parent().show();

                this.image_details_kernel.hide().parent().hide();
                if (image.get("kernel"))
                    this.image_details_kernel.text(image.get("kernel")).show().parent().show();

                this.image_details_size.hide().parent().hide();
                var size = image.get_readable_size();
                if (size && size != "unknown")
                    this.image_details_size.text(size).show().parent().show();

                this.image_details_vm.hide().parent().hide();
                var vm_name = (vm = image.get_vm()) ? vm.get("name") : undefined;
                if (vm_name)
                    this.image_details_vm.text(_(vm_name).truncate(4)).show().parent().show();

                this.image_details_gui.hide().parent().hide();
                if (image.get("GUI"))
                    this.image_details_gui.text(image.get("GUI")).show().parent().show();

            } else {
                this.image_details.hide();
            }

            this.validate();
        },

        reset_images: function() {
            this.images_list.find("li").remove();
            _.each(this.images, _.bind(function(img){
                this.add_image(img);
            }, this))
            
            if (this.images.length) {
                this.images_list.parent().find(".empty").hide();
            } else {
                this.images_list.parent().find(".empty").show();
            }

            this.select_image();
            
            var self = this;
            this.images_list.find(".image-details").click(function(){
                self.select_image($(this).data("image"));
            });
            
        },

        show: function() {
            views.CreateImageSelectView.__super__.show.apply(this, arguments);
        },

        add_image: function(img) {
            var image = $(('<li id="create-vm-image-{1}"' +
                           'class="image-details clearfix">{2}{0}' +
                           '<p>{4}</p><span class="size">{3}' +
                           '</span></li>').format(img.get("name"), 
                                                  img.id, 
                                                  snf.ui.helpers.os_icon_tag(img.get("OS")),
                                                  img.get_readable_size(),
                                                  util.truncate(img.get("description"), 35)));
            image.data("image", img);
            image.data("image_id", img.id);
            this.images_list.append(image);
        },

        reset: function() {
            this.select_type("system");
        },

        get: function() {
            return {'image': this.selected_image};
        },

        validate: function() {
            if (!this.selected_image) {
                this.parent.$(".form-action.next").hide();
            } else {
                this.parent.$(".form-action.next").show();
            }
        }
    });

    views.CreateFlavorSelectView = views.CreateVMStepView.extend({
        step: 2,
        initialize: function() {
            views.CreateFlavorSelectView.__super__.initialize.apply(this, arguments);
            this.parent.bind("image:change", _.bind(this.handle_image_change, this));

            this.cpus = this.$(".flavors-cpu-list");
            this.disks = this.$(".flavors-disk-list");
            this.disk_templates = this.$(".flavors-disk-template-list");
            this.mems = this.$(".flavors-mem-list");

            this.predefined_flavors = SUGGESTED_FLAVORS;
            this.predefined_flavors_keys = _.keys(SUGGESTED_FLAVORS);
            this.predefined_flavors_keys = _.sortBy(this.predefined_flavors_keys, _.bind(function(k){
                var flv = this.predefined_flavors[k];
                return (flv.ram * flv.cpu * flv.disk) + flv.disk_template;
            }, this));

            this.predefined = this.$(".predefined-list");
            this.update_predefined_flavors();
        },

        handle_image_change: function(data) {
            this.current_image = data;
            this.update_valid_predefined();
            this.update_flavors_data();
            this.reset_flavors();
            this.update_layout();
        },

        validate_selected_flavor: function() {
            if (!this.flavor_is_valid(this.current_flavor)) {
                this.select_valid_flavor();
            }
        },

        reset_flavors: function() {
            this.$(".flavor-opts-list .option").remove();
            this.create_flavors();
        },

        update_predefined_flavors: function() {
            this.predefined.find("li").remove();
            _.each(this.predefined_flavors_keys, _.bind(function(key) {
                var val = this.predefined_flavors[key];
                var el = $(('<li class="predefined-selection" id="predefined-flavor-{0}">' +
                           '{1}</li>').format(key, _(key).capitalize()));

                this.predefined.append(el);
                el.data({flavor: storage.flavors.get_flavor(val.cpu, val.ram, val.disk, val.disk_template, this.flavors)});
                el.click(_.bind(function() {
                    this.handle_predefined_click(el);
                }, this))
            }, this));
            this.update_valid_predefined();
        },

        handle_predefined_click: function(el) {
            if (el.hasClass("disabled")) { return };
            this.set_current(el.data("flavor"));
        },

        select_valid_flavor: function() {
            var found = false;
            var self = this;
            _.each(this.flavors, function(flv) {
                if (self.flavor_is_valid(flv)) {
                    found = flv;
                    return false;
                }
            });
            
            if (found) {
                this.set_current(found);
            } else {
                this.current_flavor = undefined;
                this.validate();
                this.$("li.predefined-selection").addClass("disabled");
                this.$(".flavor-opts-list li").removeClass("selected");
            }
        },

        update_valid_predefined: function() {
            this.update_unavailable_values();
            var self = this;
            this.valid_predefined = _.select(_.map(this.predefined_flavors, function(flv, key){
                var existing = storage.flavors.get_flavor(flv.cpu, flv.ram, flv.disk, flv.disk_template, self.flavors);
                // non existing
                if (!existing) {
                    return false;
                }
                
                // not available for image
                if (self.unavailable_values && self.unavailable_values.disk.indexOf(existing.get_disk_size()) > -1) {
                    return false
                }

                return key;
            }), function(ret) { return ret });
            
            $("li.predefined-selection").addClass("disabled");
            _.each(this.valid_predefined, function(key) {
                $("#predefined-flavor-" + key).removeClass("disabled");
            })
        },

        update_selected_predefined: function() {
            var self = this;
            this.predefined.find("li").removeClass("selected");

            _.each(this.valid_predefined, function(key){
                var flv = self.predefined_flavors[key];
                var exists = storage.flavors.get_flavor(flv.cpu, flv.ram, flv.disk, flv.disk_template, self.flavors);

                if (exists && (exists.id == self.current_flavor.id)) {
                    $("#predefined-flavor-" + key).addClass("selected");
                }
            })
        },
        
        update_flavors_data: function() {
            this.flavors = storage.flavors.active();
            this.flavors_data = storage.flavors.get_data(this.flavors);
            
            var self = this;
            var set = false;
            
            // FIXME: validate current flavor
            
            if (!this.current_flavor) {
                _.each(this.valid_predefined, function(key) {
                    var flv = self.predefined_flavors[key];
                    var exists = storage.flavors.get_flavor(flv.cpu, flv.ram, flv.disk, flv.disk_template, self.flavors);
                    if (exists && !set) {
                        self.set_current(exists);
                        set = true;
                    }
                })
            }

            this.update_unavailable_values();
        },

        update_unavailable_values: function() {
            if (!this.current_image) { this.unavailable_values = {disk:[], ram:[], cpu:[]}; return };
            this.unavailable_values = storage.flavors.unavailable_values_for_image(this.current_image);
        },
        
        flavor_is_valid: function(flv) {
            if (!flv) { return false };

            var existing = storage.flavors.get_flavor(flv.get("cpu"), flv.get("ram"), flv.get("disk"), flv.get("disk_template"), this.flavors);
            if (!existing) { return false };
            
            if (this.unavailable_values && (this.unavailable_values.disk.indexOf(parseInt(flv.get("disk")) * 1000) > -1)) {
                return false;
            }
            return true;
        },
            
        set_valid_current_for: function(t, val) {
            var found = this.flavors[0];
            _.each(this.flavors, function(flv) {
                if (flv.get(t) == val) {
                    found = flv;
                }
            });

            this.set_current(found);
            this.validate_selected_flavor();
        },

        set_current: function(flv) {

            if (!flv) {
                // user clicked on invalid combination
                // force the first available choice for the
                // type of option he last clicked
                this.set_valid_current_for.apply(this, this.last_choice);
                return;
            }

            this.current_flavor = flv;
            this.trigger("change");
            if (this.current_flavor) {
                this.update_selected_flavor();
                this.update_selected_predefined();
            }
            
            this.validate();
        },
        
        select_default_flavor: function() {
               
        },

        update_selected_from_ui: function() {
            this.set_current(this.ui_selected());
        },
        
        update_disabled_flavors: function() {
            this.$(".flavor-options.disk li").removeClass("disabled");
            if (!this.unavailable_values) { return }
            
            this.$("#create-vm-flavor-options .flavor-options.disk li").each(_.bind(function(i, el){
                var el_value = $(el).data("value") * 1000;
                if (this.unavailable_values.disk.indexOf(el_value) > -1) {
                    $(el).addClass("disabled");
                };
            }, this));
        },

        create_flavors: function() {
            var flavors = this.get_active_flavors();
            var valid_flavors = this.get_valid_flavors();
            this.__added_flavors = {'cpu':[], 'ram':[], 'disk':[], 'disk_template':[] };

            _.each(flavors, _.bind(function(flv){
                this.add_flavor(flv);
            }, this));
            
            this.sort_flavors(this.disks);
            this.sort_flavors(this.cpus);
            this.sort_flavors(this.mems);
            this.sort_flavors(this.disk_templates);

            var self = this;
            this.$(".flavor-options li.option").click(function(){
                var el = $(this);

                if (el.hasClass("disabled")) { return }

                el.parent().find(".option").removeClass("selected");
                el.addClass("selected");
                
                if (el.hasClass("mem")) { self.last_choice = ["ram", $(this).data("value")] }
                if (el.hasClass("cpu")) { self.last_choice = ["cpu", $(this).data("value")] }
                if (el.hasClass("disk")) { self.last_choice = ["disk", $(this).data("value")] }
                if (el.hasClass("disk_template")) { self.last_choice = ["disk_template", $(this).data("value")] }

                self.update_selected_from_ui();
            })

            //this.$(".flavor-options li.disk_template.option").mouseover(function(){
                //$(this).parent().find(".description").hide();
                //$(this).find(".description").show();
            //}).mouseout(function(){
                //$(this).parent().find(".description").hide();
                //$(this).parent().find(".selected .description").show();
            //});
        },

        sort_flavors: function(els) {
            var prev = undefined;
            els.find("li").each(function(i,el){
                el = $(el);
                if (!prev) { prev = el; return true };
                if (el.data("value") < prev.data("value")) {
                    prev.before(el);
                }
                prev = el;
            })
        },
        
        ui_selected: function() {
            var args = [this.$(".option.cpu.selected").data("value"), 
                this.$(".option.mem.selected").data("value"), 
                this.$(".option.disk.selected").data("value"),
                this.$(".option.disk_template.selected").data("value"),
            this.flavors];

            var flv = storage.flavors.get_flavor.apply(storage.flavors, args);
            return flv;
        },

        update_selected_flavor: function() {
            var flv = this.current_flavor;
            if (!flv) { return }
            this.$(".option").removeClass("selected");

            this.$(".option.cpu.value-" + flv.get("cpu")).addClass("selected");
            this.$(".option.mem.value-" + flv.get("ram")).addClass("selected");
            this.$(".option.disk.value-" + flv.get("disk")).addClass("selected");
            this.$(".option.disk_template.value-" + flv.get("disk_template")).addClass("selected");
            
            var disk_el = this.$(".option.disk_template.value-" + flv.get("disk_template"));
            var basebgpos = 470;
                
            var append_to_bg_pos = 40 + (disk_el.index() * 91);
            var bg_pos = basebgpos - append_to_bg_pos;

            this.$(".disk-template-description").css({backgroundPosition:'-' + bg_pos + 'px top'})
            this.$(".disk-template-description p").html(flv.get_disk_template_info().description || "");
        },
        
        __added_flavors: {'cpu':[], 'ram':[], 'disk':[], 'disk_template':[]},
        add_flavor: function(flv) {
            var values = {'cpu': flv.get('cpu'), 
                          'mem': flv.get('ram'), 
                          'disk': flv.get('disk'), 
                          'disk_template': flv.get('disk_template')};

            disabled = "";
            
            if (this.__added_flavors.cpu.indexOf(values.cpu) == -1) {
                var cpu = $(('<li class="option cpu value-{0} {1}">' + 
                             '<span class="value">{0}</span>' + 
                             '<span class="metric">x</span></li>').format(values.cpu, disabled)).data('value', values.cpu);
                this.cpus.append(cpu);
                this.__added_flavors.cpu.push(values.cpu);
            }

            if (this.__added_flavors.ram.indexOf(values.mem) == -1) {
                var mem = $(('<li class="option mem value-{0}">' + 
                             '<span class="value">{0}</span>' + 
                             '<span class="metric">MB</span></li>').format(values.mem)).data('value', values.mem);
                this.mems.append(mem);
                this.__added_flavors.ram.push(values.mem);
            }

            if (this.__added_flavors.disk.indexOf(values.disk) == -1) {
                var disk = $(('<li class="option disk value-{0}">' + 
                              '<span class="value">{0}</span>' + 
                              '<span class="metric">GB</span></li>').format(values.disk)).data('value', values.disk);
                this.disks.append(disk);
                this.__added_flavors.disk.push(values.disk)
            }
            
            if (this.__added_flavors.disk_template.indexOf(values.disk_template) == -1) {
                var template_info = flv.get_disk_template_info();
                var disk_template = $(('<li title="{2}" class="option disk_template value-{0}">' + 
                                       '<span class="value name">{1}</span>' +
                                       '</li>').format(values.disk_template, 
                                            template_info.name, 
                                            template_info.description)).data('value', 
                                                                values.disk_template);

                this.disk_templates.append(disk_template);
                //disk_template.tooltip({position:'top center', offset:[-5,0], delay:100, tipClass:'tooltip disktip'});
                this.__added_flavors.disk_template.push(values.disk_template)
            }
            
        },
        
        get_active_flavors: function() {
            return storage.flavors.active();
        },

        get_valid_flavors: function() {
            return this.flavors;
        },

        update_layout: function() {
            this.update_selected_flavor();
            this.update_disabled_flavors();
            this.validate();
            this.validate_selected_flavor();
        },

        reset: function() {
            this.current_image = storage.images.at(0);
            this.flavors = [];
            this.flavors_data = {'cpu':[], 'mem':[], 'disk':[]};
            this.update_flavors_data();
        },

        validate: function() {
            if (!this.current_flavor) {
                this.parent.$(".form-action.next").hide();
            } else {
                this.parent.$(".form-action.next").show();
            }
        },

        get: function() {
            return {'flavor': this.current_flavor}
        }

    });

    views.CreatePersonalizeView = views.CreateVMStepView.extend({
        step: 3,
        initialize: function() {
            views.CreateSubmitView.__super__.initialize.apply(this, arguments);
            this.roles = this.$("li.predefined-meta.role .values");
            this.name = this.$("input.rename-field");
            this.name_changed = false;
            this.init_suggested_roles();
            this.init_handlers();
            this.ssh_list = this.$(".ssh ul");
            this.selected_keys = [];

            var self = this;
            this.$(".create-ssh-key").click(function() {
                var confirm_close = true;
                if (confirm_close) {
                    snf.ui.main.public_keys_view.show(self.parent);
                } else {
                }
            });
        },

        init_suggested_roles: function() {
            var cont = this.roles;
            cont.empty();
            
            // TODO: get suggested from snf.api.conf
            _.each(window.SUGGESTED_ROLES, function(r){
                var el = $('<span class="val">{0}</span>'.format(r));
                el.data("value", r);
                cont.append(el);
                el.click(function() {
                    $(this).parent().find(".val").removeClass("selected");
                    $(this).toggleClass("selected");
                })
            });
            
            var self = this;
            $(".ssh li.ssh-key-option").live("click", function(e) {
                var key = $(this).data("model");
                self.select_key(key);
            });
        },

        select_key: function(key) {
            var exists = this.selected_keys.indexOf(key.id);
            if (exists > -1) {
                this.selected_keys.splice(exists, 1);
            } else {
                this.selected_keys.push(key.id);
            }
            this.update_ui_keys_selections(this.selected_keys);
        },

        update_ui_keys_selections: function(keys) {
            var self = this;
            self.$(".ssh-key-option").removeClass("selected");
            self.$(".ssh-key-option .check").attr("checked", false);
            _.each(keys, function(kid) {
                $("#ssh-key-option-" + kid).addClass("selected");
                $("#ssh-key-option-" + kid).find(".check").attr("checked", true);
            });
        },

        update_ssh_keys: function() {
            this.ssh_list.empty();
            var keys = snf.storage.keys.models;
            if (keys.length == 0) { 
                this.$(".ssh .empty").show();
            } else {
                this.$(".ssh .empty").hide();
            }
            _.each(keys, _.bind(function(key){
                var el = $('<li id="ssh-key-option-{1}" class="ssh-key-option">{0}</li>'.format(key.get("name"), key.id));
                var check = $('<input class="check" type="checkbox"></input>')
                el.append(check);
                el.data("model", key);
                this.ssh_list.append(el);
            }, this));
        },

        init_handlers: function() {
            this.name.bind("keypress", _.bind(function(e) {
                this.name_changed = true;
                if (e.keyCode == 13) { this.parent.set_step(4); this.parent.update_layout() };    
            }, this));

            this.name.bind("click", _.bind(function() {
                if (!this.name_changed) {
                    this.name.val("");
                }
            }, this))
        },

        show: function() {
            views.CreatePersonalizeView.__super__.show.apply(this, arguments);
            this.update_layout();
        },
        
        update_layout: function() {
            var params = this.parent.get_params();

            if (!params.image || !params.flavor) { return }

            if (!params.image) { return }
            var vm_name_tpl = snf.config.vm_name_template || "My {0} server";
            var vm_name = vm_name_tpl.format(params.image.get("name"));
            var orig_name = vm_name;
            
            var existing = true;
            var j = 0;

            while (existing && !this.name_changed) {
                var existing = storage.vms.select(function(vm){return vm.get("name") == vm_name}).length
                if (existing) {
                    j++;
                    vm_name = orig_name + " " + j;
                }
            }

            if (!_(this.name.val()).trim() || !this.name_changed) {
                this.name.val(vm_name);
            }

            if (!this.name_changed && this.parent.visible()) {
                if (!$.browser.msie && !$.browser.opera) {
                    this.$("#create-vm-name").select();
                } else {
                    window.setTimeout(_.bind(function(){
                        this.$("#create-vm-name").select();
                    }, this), 400)
                }
            }
            
            var img = snf.ui.helpers.os_icon_path(params.image.get("OS"))
            this.name.css({backgroundImage:"url({0})".format(img)})
            
            if (!params.image.supports('ssh')) {
                this.disable_ssh_keys();
            } else {
                this.enable_ssh_keys();
                this.update_ssh_keys();
            }

            this.update_ui_keys_selections(this.selected_keys);
        },

        disable_ssh_keys: function() {
            this.$(".disabled.desc").show();
            this.$(".empty.desc").hide();
            this.$(".ssh .confirm-params").hide();
            this.selected_keys = [];
        },

        enable_ssh_keys: function() {
            this.$(".ssh .confirm-params").show();
            this.$(".disabled.desc").hide();
        },

        reset: function() {
            this.roles.find(".val").removeClass("selected");
            this.name_changed = false;
            this.selected_keys = [];
            this.update_layout();
        },

        get_meta: function() {
            if (this.roles.find(".selected").length == 0) {
                return false;
            }

            var role = $(this.roles.find(".selected").get(0)).data("value");
            return {'Role': role }
        },

        get: function() {
            var val = {'name': this.name.val() };
            if (this.get_meta()) {
                val.metadata = this.get_meta();
            }

            val.keys = _.map(this.selected_keys, function(k){ return snf.storage.keys.get(k)});
            
            return val;
        }
    });

    views.CreateSubmitView = views.CreateVMStepView.extend({
        step: 4,
        initialize: function() {
            views.CreateSubmitView.__super__.initialize.apply(this, arguments);
            this.roles = this.$("li.predefined-meta.role .values");
            this.confirm = this.$(".confirm-params ul");
            this.name = this.$("h3.vm-name");
            this.keys = this.$(".confirm-params.ssh");
            this.meta = this.$(".confirm-params.meta");
            this.init_handlers();
        },

        init_handlers: function() {
        },

        show: function() {
            views.CreateSubmitView.__super__.show.apply(this, arguments);
            this.update_layout();
        },
        
        update_flavor_details: function() {
            var flavor = this.parent.get_params().flavor;

            function set_detail(sel, key) {
                var val = key;
                if (key == undefined) { val = flavor.get(sel) };
                this.$(".confirm-cont.flavor .flavor-" + sel + " .value").text(val)
            }
            
            set_detail("cpu", flavor.get("cpu") + "x");
            set_detail("ram", flavor.get("ram") + " MB");
            set_detail("disk", util.readablizeBytes(flavor.get("disk") * 1024 * 1024 * 1024));
            set_detail("disktype", flavor.get_disk_template_info().name);
        },

        update_image_details: function() {
            var image = this.parent.get_params().image;

            function set_detail(sel, key) {
                var val = key;
                if (key == undefined) { val = image.get(sel) };
                this.$(".confirm-cont.image .image-" + sel + " .value").text(val)
            }
            
            set_detail("description");
            set_detail("name");
            set_detail("os", _(image.get("OS")).capitalize());
            set_detail("gui", image.get("GUI"));
            set_detail("size", image.get_readable_size());
            set_detail("kernel");
        },

        update_selected_keys: function(keys) {
            this.keys.empty();
            if (!keys || keys.length == 0) {
                this.keys.append(this.make("li", {'class':'empty'}, 'No keys selected'))
            }
            _.each(keys, _.bind(function(key) {
                var el = this.make("li", {'class':'selected-ssh-key'}, key.get('name'));
                this.keys.append(el);
            }, this))
        },

        update_selected_meta: function(meta) {
            this.meta.empty();
            if (!meta || meta.length == 0) {
                this.meta.append(this.make("li", {'class':'empty'}, 'No tags selected'))
            }
            _.each(meta, _.bind(function(value, key) {
                var el = this.make("li", {'class':"confirm-value"});
                var name = this.make("span", {'class':"ckey"}, key);
                var value = this.make("span", {'class':"cval"}, value);

                $(el).append(name)
                $(el).append(value);
                this.meta.append(el);
            }, this));
        },

        update_layout: function() {
            var params = this.parent.get_params();
            if (!params.image || !params.flavor) { return }

            if (!params.image) { return }

            this.name.text(params.name);

            this.confirm.find("li.image .value").text(params.flavor.get("image"));
            this.confirm.find("li.cpu .value").text(params.flavor.get("cpu") + "x");
            this.confirm.find("li.mem .value").text(params.flavor.get("ram"));
            this.confirm.find("li.disk .value").text(params.flavor.get("disk"));

            var img = snf.ui.helpers.os_icon_path(params.image.get("OS"))
            this.name.css({backgroundImage:"url({0})".format(img)})

            this.update_image_details();
            this.update_flavor_details();

            if (!params.image.supports('ssh')) {
                this.keys.hide();
                this.keys.prev().hide();
            } else {
                this.keys.show();
                this.keys.prev().show();
                this.update_selected_keys(params.keys);
            }
            
            this.update_selected_meta(params.metadata);
        },

        reset: function() {
            this.update_layout();
        },

        get_meta: function() {
        },

        get: function() {
            return {};
        }
    });

    views.CreateVMView = views.Overlay.extend({
        
        view_id: "create_vm_view",
        content_selector: "#createvm-overlay-content",
        css_class: 'overlay-createvm overlay-info',
        overlay_id: "metadata-overlay",

        subtitle: false,
        title: "Create new machine",

        initialize: function(options) {
            views.CreateVMView.__super__.initialize.apply(this);
            this.current_step = 1;

            this.password_view = new views.VMCreationPasswordView();

            this.steps = [];
            this.steps[1] = new views.CreateImageSelectView(this);
            this.steps[1].bind("change", _.bind(function(data) {this.trigger("image:change", data)}, this));

            this.steps[2] = new views.CreateFlavorSelectView(this);
            this.steps[3] = new views.CreatePersonalizeView(this);
            this.steps[4] = new views.CreateSubmitView(this);

            this.cancel_btn = this.$(".create-controls .cancel");
            this.next_btn = this.$(".create-controls .next");
            this.prev_btn = this.$(".create-controls .prev");
            this.submit_btn = this.$(".create-controls .submit");

            this.history = this.$(".steps-history");
            this.history_steps = this.$(".steps-history .steps-history-step");
            
            this.init_handlers();
        },

        init_handlers: function() {
            var self = this;
            this.next_btn.click(_.bind(function(){
                this.set_step(this.current_step + 1);
                this.update_layout();
            }, this))
            this.prev_btn.click(_.bind(function(){
                this.set_step(this.current_step - 1);
                this.update_layout();
            }, this))
            this.cancel_btn.click(_.bind(function(){
                this.close_all();
            }, this))
            this.submit_btn.click(_.bind(function(){
                this.submit();
            }, this))
            
            this.history.find(".completed").live("click", function() {
                var step = parseInt($(this).attr("id").replace("vm-create-step-history-", ""));
                self.set_step(step);
                self.update_layout();
            })
        },

        set_step: function(st) {
        },
        
        validate: function(data) {
            if (_(data.name).trim() == "") {
                this.$(".form-field").addClass("error");
                return false;
            } else {
                return true;
            }
        },

        submit: function() {
            if (this.submiting) { return };
            var data = this.get_params();
            var meta = {};
            var extra = {};
            var personality = [];

            if (this.validate(data)) {
                this.submit_btn.addClass("in-progress");
                this.submiting = true;
                if (data.metadata) { meta = data.metadata; }
                if (data.keys && data.keys.length > 0) {
                    personality.push(data.image.personality_data_for_keys(data.keys))
                }

                extra['personality'] = personality;
                storage.vms.create(data.name, data.image, data.flavor, meta, extra, _.bind(function(data){
                    this.close_all();
                    this.password_view.show(data.server.adminPass, data.server.id);
                    this.submiting = false;
                }, this));
            }
        },

        close_all: function() {
            this.hide();
        },

        reset: function() {
            this.current_step = 1;

            this.steps[1].reset();
            this.steps[2].reset();
            this.steps[3].reset();
            this.steps[4].reset();

            this.steps[1].show();
            this.steps[2].show();
            this.steps[3].show();
            this.steps[4].show();

            this.submit_btn.removeClass("in-progress");
        },

        onShow: function() {
        },

        update_layout: function() {
            this.show_step(this.current_step);
            this.current_view.update_layout();
        },

        beforeOpen: function() {
            if (!this.skip_reset_on_next_open) {
                this.submiting = false;
                this.reset();
                this.current_step = 1;
                this.$(".steps-container").css({"margin-left":0 + "px"});
                this.show_step(1);
            }
            
            this.skip_reset_on_next_open = false;
            this.update_layout();
        },
        
        set_step: function(step) {
            if (step <= 1) {
                step = 1
            }
            if (step > this.steps.length - 1) {
                step = this.steps.length - 1;
            }
            this.current_step = step;
        },

        show_step: function(step) {
            this.current_view = this.steps[step];
            this.update_controls();

            this.steps[step].show();
            var width = this.el.find('.container').width();
            var left = (step -1) * width * -1;
            this.$(".steps-container").animate({"margin-left": left + "px"}, 300);

            this.update_steps_history();
        },

        update_steps_history: function() {
            var self = this;
            function get_step(s) {
                return self.history.find(".step" + s + "h");
            }
            
            var current_step = parseInt(this.current_view.step);
            _.each(this.steps, function(stepv) {
                var step = parseInt(stepv.step);
                get_step(step).removeClass("completed").removeClass("current");
                if (step == current_step) {
                    get_step(step).removeClass("completed").addClass("current");
                }
                if (step < current_step) {
                    get_step(step).removeClass("current").addClass("completed");
                }
            });
        },

        update_controls: function() {
            var step = this.current_step;
            if (step == 1) {
                this.prev_btn.hide();
                this.cancel_btn.show();
            } else {
                this.prev_btn.show();
                this.cancel_btn.hide();
            }
            
            if (step == this.steps.length - 1) {
                this.next_btn.hide();
                this.submit_btn.show();
            } else {
                this.next_btn.show();
                this.submit_btn.hide();
            }
        },

        get_params: function() {
            return _.extend({}, this.steps[1].get(), this.steps[2].get(), this.steps[3].get());
        }
    });
    
})(this);

